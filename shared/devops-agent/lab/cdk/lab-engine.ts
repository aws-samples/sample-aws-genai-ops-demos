/**
 * LabEngine: the Lambda durable function that runs one injected scenario end to end
 * (inject -> wait up to the scenario's auto-revert timeout for a rollback -> revert),
 * plus the plain API Lambda that drives it. Mechanism only: no API Gateway, no routes,
 * no UI. The demo owns those (see observability/eks-investigation-devops-agent for the
 * reference) and passes in what its handlers need.
 *
 * One code bundle, two functions, one role:
 *   shared/devops-agent/lab/lambda/*.py + requirements.txt   engine.py (the durable mechanism), devops_agent.py
 *   <demo>/lab/*.py                             the demo's handlers, API, entry points
 *   <demo>/lab/*.yaml                           the demo's scenario definitions
 * copied flat into a staging folder at synth time, then pip-installed for the Lambda
 * runtime without Docker (falls back to the CDK Docker image). Demo files must not be
 * named engine.py or devops_agent.py.
 *
 * Durable-function facts encoded here: the function needs `durableConfig` and the
 * AWSLambdaBasicDurableExecutionRolePolicy; it must be invoked through a qualified ARN
 * (the `live` alias); the API's grant on it is built from the function NAME, because
 * granting with the ARN token makes the shared role's policy depend on the function
 * while the function depends on the policy (a CloudFormation cycle).
 *
 * The construct lives outside the demo's node_modules: the demo's CDK project must map
 * `aws-cdk-lib` and `constructs` to its own copies (tsconfig `paths` + `tsconfig-paths`
 * for ts-node + jest `moduleNameMapper`), see shared/devops-agent/README.md.
 */
import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { Construct } from 'constructs';
import { spawnSync } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';

// One place for the Lambda Python version: runtime of both functions and the pip wheel target.
export const PYTHON_RUNTIME = lambda.Runtime.PYTHON_3_14;
const PYTHON_VERSION = '3.14';
const SHARED_LAMBDA_DIR = path.join(__dirname, '..', 'lambda');
const SHARED_MODULES = ['engine', 'devops_agent'];

export interface LabEngineProps {
  /** The demo's lab folder: handlers, API, entry points, scenario definitions. */
  labDir: string;
  /** Handler of the durable function, e.g. `engine_main.handler`. */
  engineHandler: string;
  /** Handler of the API function, e.g. `api.handler`. */
  apiHandler: string;
  /** Prefix for function and role names, e.g. `${projectName}-${environment}`. */
  namePrefix: string;
  /** Fixed IAM role name, when deploy scripts need to find the role by name. */
  roleName?: string;
  /** Environment variables both functions need (cluster name, alarm names, ...). */
  environment?: Record<string, string>;
  /** Extra IAM statements for what the handlers touch. */
  policyStatements?: iam.PolicyStatement[];
  managedPolicies?: iam.IManagedPolicy[];
  /** Layers the handlers need (kubectl, ...). */
  layers?: lambda.ILayerVersion[];
  /** VPC placement, when the handlers must reach private endpoints. */
  vpc?: ec2.IVpc;
  vpcSubnets?: ec2.SubnetSelection;
  securityGroups?: ec2.ISecurityGroup[];
  /** Defaults: engine 2 min, api 60 s, 512 MB. */
  engineTimeout?: cdk.Duration;
  apiTimeout?: cdk.Duration;
  memorySize?: number;
  /** Ceiling for one engine execution (default 1 hour; must exceed the longest auto-revert). */
  executionTimeout?: cdk.Duration;
  /** Staging directory for the bundle (default `<cwd>/.lab-stage`; add it to .gitignore). */
  stageDir?: string;
}

function stage(labDir: string, stageDir: string): void {
  fs.rmSync(stageDir, { recursive: true, force: true });
  fs.mkdirSync(stageDir, { recursive: true });
  for (const file of fs.readdirSync(SHARED_LAMBDA_DIR)) {
    if (file.endsWith('.py') || file === 'requirements.txt') {
      fs.copyFileSync(path.join(SHARED_LAMBDA_DIR, file), path.join(stageDir, file));
    }
  }
  for (const file of fs.readdirSync(labDir)) {
    if (file.endsWith('.py') && SHARED_MODULES.includes(path.basename(file, '.py'))) {
      throw new Error(`${labDir}/${file} collides with a shared Lab module; rename it.`);
    }
    if (file.endsWith('.py') || file.endsWith('.yaml')) {
      fs.copyFileSync(path.join(labDir, file), path.join(stageDir, file));
    }
  }
}

/** pip resolves Linux/x86_64 wheels for the Lambda runtime; false lets CDK fall back to Docker. */
function bundleLocally(stageDir: string, outputDir: string): boolean {
  const pipArgs = [
    '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check',
    '--platform', 'manylinux2014_x86_64', '--only-binary=:all:',
    '--python-version', PYTHON_VERSION, '--implementation', 'cp',
    '--target', outputDir,
    '-r', path.join(stageDir, 'requirements.txt'),
  ];
  const installed = ['python', 'python3'].some(python => spawnSync(python, pipArgs, { stdio: ['ignore', 'pipe', 'pipe'] }).status === 0);
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

export class LabEngine extends Construct {
  /** Shared by both functions; grant it what the handlers need. */
  public readonly role: iam.Role;
  public readonly engineFunction: lambda.Function;
  /** Invoke the engine through this (durable functions need a qualified ARN). */
  public readonly engineAlias: lambda.Alias;
  public readonly apiFunction: lambda.Function;

  constructor(scope: Construct, id: string, props: LabEngineProps) {
    super(scope, id);
    const stack = cdk.Stack.of(this);
    const stageDir = props.stageDir ?? path.join(process.cwd(), '.lab-stage');
    stage(props.labDir, stageDir);

    const code = lambda.Code.fromAsset(stageDir, {
      bundling: {
        image: PYTHON_RUNTIME.bundlingImage,
        command: ['bash', '-c', 'pip install --quiet -r requirements.txt -t /asset-output && cp *.py *.yaml /asset-output/'],
        local: { tryBundle: (outputDir: string) => bundleLocally(stageDir, outputDir) },
      },
    });

    this.role = new iam.Role(this, 'Role', {
      roleName: props.roleName,
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicDurableExecutionRolePolicy'),
        ...(props.vpc ? [iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaVPCAccessExecutionRole')] : []),
        ...(props.managedPolicies ?? []),
      ],
    });
    // devops_agent.py: read-only data-plane calls the Lab shows (tasks, usage, is the skill registered).
    this.role.addToPolicy(new iam.PolicyStatement({
      sid: 'DevOpsAgentReadOnly',
      actions: ['aidevops:GetAccountUsage', 'aidevops:ListBacklogTasks', 'aidevops:ListExecutions', 'aidevops:ListJournalRecords', 'aidevops:ListAssets'],
      resources: ['*'],
    }));
    for (const statement of props.policyStatements ?? []) {
      this.role.addToPolicy(statement);
    }

    const engineFunctionName = `${props.namePrefix}-lab-engine`;
    const engineFunctionArn = stack.formatArn({
      service: 'lambda', resource: 'function', resourceName: engineFunctionName,
      arnFormat: cdk.ArnFormat.COLON_RESOURCE_NAME,
    });
    this.role.addToPolicy(new iam.PolicyStatement({
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

    const common = {
      runtime: PYTHON_RUNTIME,
      code,
      role: this.role,
      layers: props.layers,
      memorySize: props.memorySize ?? 512,
      vpc: props.vpc,
      vpcSubnets: props.vpcSubnets,
      securityGroups: props.securityGroups,
    };

    this.engineFunction = new lambda.Function(this, 'Engine', {
      ...common,
      functionName: engineFunctionName,
      description: 'Demo Lab engine: inject -> wait for rollback -> revert (Lambda durable function)',
      handler: props.engineHandler,
      timeout: props.engineTimeout ?? cdk.Duration.minutes(2),
      environment: props.environment,
      durableConfig: {
        executionTimeout: props.executionTimeout ?? cdk.Duration.hours(1),
        retentionPeriod: cdk.Duration.days(7),
      },
    });
    this.engineAlias = new lambda.Alias(this, 'EngineLiveAlias', {
      aliasName: 'live',
      version: this.engineFunction.currentVersion,
    });

    this.apiFunction = new lambda.Function(this, 'Api', {
      ...common,
      functionName: `${props.namePrefix}-lab-api`,
      description: 'Demo Lab API: drives the engine, serves the Lab UI',
      handler: props.apiHandler,
      timeout: props.apiTimeout ?? cdk.Duration.seconds(60),
      environment: { ...(props.environment ?? {}), ENGINE_FUNCTION_ARN: this.engineAlias.functionArn },
    });
  }
}
