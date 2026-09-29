/**
 * LabBackend: the DevOps Agent Demo Lab backend as one CDK construct.
 *
 * One code bundle, two Lambda functions:
 *  - engine: Lambda durable function running one injected scenario end to end
 *    (inject -> wait up to autoRevertSeconds for a rollback -> revert). Invoked through
 *    the `live` alias (durable functions need a qualified ARN).
 *  - api: plain function behind API Gateway serving the Lab UI (scenario definitions,
 *    live facts from the demo's probes, start/stop of engine executions, DevOps Agent
 *    tasks and usage).
 *
 * The bundle is assembled at synth time from three sources, all copied flat:
 *   shared/lab/lambda/*.py + requirements.txt     the generic Lab (this folder's sibling)
 *   <demo>/lab/*.py                               the demo's handlers.py (+ helpers)
 *   <demo>/lab/scenarios.yaml                     the demo's scenario definitions
 * so demo file names must not collide with the shared modules (engine, index, scenarios,
 * facts, devops_agent, handler_contract).
 *
 * The demo owns what its handlers need and passes it in: VPC placement, layers (kubectl),
 * extra environment variables (cluster name, alarm names) and IAM statements. The role is
 * created here (fixed name optional) and exposed, so deploy scripts can grant it access
 * (an EKS access entry, for example).
 *
 * Import from a demo:  import { LabBackend } from '../../../../shared/lab/cdk/lab-backend';
 * The demo's tsconfig must map `aws-cdk-lib` and `constructs` to its own node_modules
 * (see shared/lab/README.md): this file lives outside the demo's dependency tree.
 */
import * as cdk from 'aws-cdk-lib';
import * as apigateway from 'aws-cdk-lib/aws-apigateway';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { Construct } from 'constructs';
import { spawnSync } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';

// One place for the Lambda Python version: runtime of both functions and the pip wheel target.
const PYTHON_RUNTIME = lambda.Runtime.PYTHON_3_14;
const PYTHON_VERSION = '3.14';
const SHARED_LAMBDA_DIR = path.join(__dirname, '..', 'lambda');
const SHARED_MODULES = ['engine', 'index', 'scenarios', 'facts', 'devops_agent', 'handler_contract', 'validate'];

export interface LabBackendProps {
  /** The demo's lab folder: scenarios.yaml + handlers.py (+ helper modules). */
  labDir: string;
  /** Prefix for function, role and API names, e.g. `${projectName}-${environment}`. */
  namePrefix: string;
  /** Fixed IAM role name, when deploy scripts need to find the role by name. */
  roleName?: string;
  /** Region of the Agent Space (may differ from the deploy region). */
  devOpsAgentRegion: string;
  /** Agent Space id; empty until the Agent Space stack has produced it. */
  devOpsAgentSpaceId: string;
  /** Name of the alarm-to-webhook Lambda, when the demo has a trigger chain (linked from the Lab). */
  triggerLambdaName?: string;
  /** Environment variables both functions need (cluster name, alarm names, ...). */
  environment?: Record<string, string>;
  /** Extra IAM statements for what the handlers touch (EKS, RDS, ...). */
  policyStatements?: iam.PolicyStatement[];
  /** Extra managed policies (VPC access, ...). */
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
  /** Ceiling for one engine execution (default 1 hour; must exceed the longest autoRevertSeconds). */
  executionTimeout?: cdk.Duration;
  /** Staging directory for the bundle (default `<cdk>/.lab-stage`, add it to .gitignore). */
  stageDir?: string;
}

function stageLab(labDir: string, stageDir: string): void {
  fs.rmSync(stageDir, { recursive: true, force: true });
  fs.mkdirSync(stageDir, { recursive: true });
  for (const file of fs.readdirSync(SHARED_LAMBDA_DIR)) {
    if (file.endsWith('.py') || file === 'requirements.txt') {
      fs.copyFileSync(path.join(SHARED_LAMBDA_DIR, file), path.join(stageDir, file));
    }
  }
  for (const file of fs.readdirSync(labDir)) {
    if (file.endsWith('.py')) {
      if (SHARED_MODULES.includes(path.basename(file, '.py'))) {
        throw new Error(`${labDir}/${file} collides with a shared Lab module; rename it.`);
      }
      fs.copyFileSync(path.join(labDir, file), path.join(stageDir, file));
    }
  }
  const scenarios = path.join(labDir, 'scenarios.yaml');
  if (!fs.existsSync(scenarios)) {
    throw new Error(`${scenarios} not found; the Lab needs the demo's scenario definitions.`);
  }
  if (!fs.existsSync(path.join(labDir, 'handlers.py'))) {
    throw new Error(`${labDir}/handlers.py not found; the Lab needs the demo's HANDLERS registry.`);
  }
  fs.copyFileSync(scenarios, path.join(stageDir, 'scenarios.yaml'));
}

/**
 * Bundle without Docker: pip resolves Linux/x86_64 wheels for the Lambda runtime (the
 * durable SDK is pure Python, PyYAML ships manylinux wheels), then the staged sources are
 * copied alongside. Returning false lets CDK fall back to the Docker image.
 */
function bundleLocally(stageDir: string, outputDir: string): boolean {
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

export class LabBackend extends Construct {
  /** Shared by both functions; grant it what the handlers need. */
  public readonly role: iam.Role;
  public readonly engineFunction: lambda.Function;
  public readonly engineAlias: lambda.Alias;
  public readonly apiFunction: lambda.Function;
  public readonly api: apigateway.RestApi;
  public readonly apiStageName = 'prod';

  constructor(scope: Construct, id: string, props: LabBackendProps) {
    super(scope, id);
    const stack = cdk.Stack.of(this);
    const stageDir = props.stageDir ?? path.join(process.cwd(), '.lab-stage');
    stageLab(props.labDir, stageDir);

    const labCode = lambda.Code.fromAsset(stageDir, {
      bundling: {
        image: PYTHON_RUNTIME.bundlingImage,
        command: ['bash', '-c', 'pip install --quiet -r requirements.txt -t /asset-output && cp *.py *.yaml /asset-output/'],
        local: { tryBundle: (outputDir: string) => bundleLocally(stageDir, outputDir) },
      },
    });

    // ------------------------------------------------------------------
    // Role, shared by engine and api
    // ------------------------------------------------------------------
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
    this.role.addToPolicy(new iam.PolicyStatement({
      sid: 'LabAlarms',
      actions: ['cloudwatch:DescribeAlarms'],
      resources: ['*'],
    }));
    this.role.addToPolicy(new iam.PolicyStatement({
      sid: 'DevOpsAgentReadOnly',
      actions: ['aidevops:GetAccountUsage', 'aidevops:ListBacklogTasks', 'aidevops:ListExecutions', 'aidevops:ListJournalRecords'],
      resources: ['*'],
    }));
    for (const statement of props.policyStatements ?? []) {
      this.role.addToPolicy(statement);
    }

    // The API drives the engine, and both share the role. Granting with the function's
    // ARN token would make the role's policy depend on the function while the function
    // depends on the policy (circular), so the ARN is built from the fixed name.
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
      code: labCode,
      role: this.role,
      layers: props.layers,
      memorySize: props.memorySize ?? 512,
      vpc: props.vpc,
      vpcSubnets: props.vpcSubnets,
      securityGroups: props.securityGroups,
    };

    // ------------------------------------------------------------------
    // Engine: Lambda durable function, one execution per injected scenario
    // ------------------------------------------------------------------
    this.engineFunction = new lambda.Function(this, 'Engine', {
      ...common,
      functionName: engineFunctionName,
      description: 'DevOps Agent Demo Lab engine: inject -> wait for rollback -> revert (Lambda durable function)',
      handler: 'engine.handler',
      timeout: props.engineTimeout ?? cdk.Duration.minutes(2),
      environment: props.environment,
      durableConfig: {
        executionTimeout: props.executionTimeout ?? cdk.Duration.hours(1),
        retentionPeriod: cdk.Duration.days(7),
      },
    });
    // Durable functions must be invoked through a qualified ARN.
    this.engineAlias = new lambda.Alias(this, 'EngineLiveAlias', {
      aliasName: 'live',
      version: this.engineFunction.currentVersion,
    });

    // ------------------------------------------------------------------
    // API function, behind API Gateway
    // ------------------------------------------------------------------
    this.apiFunction = new lambda.Function(this, 'Api', {
      ...common,
      functionName: `${props.namePrefix}-lab-api`,
      description: 'DevOps Agent Demo Lab API: scenarios, live status, inject/rollback, agent tasks, usage',
      handler: 'index.handler',
      timeout: props.apiTimeout ?? cdk.Duration.seconds(60),
      environment: {
        ...(props.environment ?? {}),
        ENGINE_FUNCTION_ARN: this.engineAlias.functionArn,
        DEVOPS_AGENT_REGION: props.devOpsAgentRegion,
        DEVOPS_AGENT_SPACE_ID: props.devOpsAgentSpaceId,
        ...(props.triggerLambdaName ? { TRIGGER_LAMBDA_NAME: props.triggerLambdaName } : {}),
      },
    });

    this.api = new apigateway.RestApi(this, 'RestApi', {
      restApiName: `${props.namePrefix}-lab-api`,
      description: 'DevOps Agent Demo Lab API',
      defaultCorsPreflightOptions: {
        allowOrigins: apigateway.Cors.ALL_ORIGINS,
        allowMethods: apigateway.Cors.ALL_METHODS,
        allowHeaders: ['Content-Type', 'Authorization'],
      },
      deployOptions: {
        stageName: this.apiStageName,
        loggingLevel: apigateway.MethodLoggingLevel.INFO,
        metricsEnabled: true,
      },
    });
    const integration = new apigateway.LambdaIntegration(this.apiFunction);
    const admin = this.api.root.addResource('admin');
    admin.addResource('status').addMethod('GET', integration);
    admin.addResource('usage').addMethod('GET', integration);
    admin.addResource('tasks').addMethod('GET', integration);
    // /admin/scenarios lists definitions; /admin/scenarios/{id}/inject drives one.
    const scenarios = admin.addResource('scenarios');
    scenarios.addMethod('GET', integration);
    const inject = scenarios.addResource('{scenarioId}').addResource('inject');
    inject.addMethod('POST', integration);
    inject.addMethod('DELETE', integration);
  }
}
