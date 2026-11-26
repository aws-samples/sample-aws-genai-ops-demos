import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import { Construct } from 'constructs';
import { spawnSync } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';
import { BEDROCK_ACTIONS, ORG_ACTIONS, SCAN_ACTIONS, SPOKE_ROLE_NAME, hubRoleName } from './scan-permissions';
import { NOVA_LITE_FOUNDATION_MODEL } from './model-id';

export interface PipelineStackProps extends cdk.StackProps {
  table: dynamodb.ITable;
  bucket: s3.IBucket;
  /** Amazon Nova Lite inference profile id, "" when none is known for the region. */
  modelId: string;
  /** Organization scans allowed: grants the org reads and the spoke assume-role, enables the scope in the API. */
  multiAccount: boolean;
  /** Explicit member accounts to scan instead of listing the organization (manual spoke deployments). */
  scanAccounts: string[];
}

const DEMO_DIR = path.join(__dirname, '..', '..');
const BACKEND_DIR = path.join(DEMO_DIR, 'backend');
const SAMPLE_FILE = path.join(DEMO_DIR, 'sample-data', 'anycompany-data.json');
const STAGE_DIR = path.join(__dirname, '..', '.backend-stage');
const PIPELINE_FUNCTION_NAME = 's3-data-protection-pipeline';
const API_FUNCTION_NAME = 's3-data-protection-api';
// Runtime, Docker bundling image and pip wheel target must agree.
const PYTHON_RUNTIME = lambda.Runtime.PYTHON_3_13;
const PYTHON_VERSION = '3.13';

/** Assemble the Lambda source tree: the s3dp package (no tests, no caches), requirements.txt and the sample
 *  as the package resource s3dp/sample_data.json. */
function stageBackend(): string {
  fs.rmSync(STAGE_DIR, { recursive: true, force: true });
  fs.mkdirSync(STAGE_DIR, { recursive: true });
  fs.cpSync(path.join(BACKEND_DIR, 's3dp'), path.join(STAGE_DIR, 's3dp'), {
    recursive: true,
    filter: (src) => {
      const base = path.basename(src);
      return base !== 'tests' && base !== '__pycache__' && !base.endsWith('.pyc');
    },
  });
  fs.copyFileSync(path.join(BACKEND_DIR, 'requirements.txt'), path.join(STAGE_DIR, 'requirements.txt'));
  fs.copyFileSync(SAMPLE_FILE, path.join(STAGE_DIR, 's3dp', 'sample_data.json'));
  return STAGE_DIR;
}

/** Bundle without Docker: pip resolves Linux/arm64 wheels for the Lambda runtime, then the package is copied
 *  alongside. Returning false lets CDK fall back to the Docker bundling image. */
function bundleBackendLocally(stageDir: string, outputDir: string): boolean {
  const pipArgs = [
    '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check', '--no-compile',
    '--platform', 'manylinux2014_aarch64', '--only-binary=:all:',
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
  fs.cpSync(path.join(stageDir, 's3dp'), path.join(outputDir, 's3dp'), { recursive: true });
  // pip leaves console scripts in bin/ and the host Python may leave caches: neither belongs in the bundle
  fs.rmSync(path.join(outputDir, 'bin'), { recursive: true, force: true });
  const dropCaches = (dir: string): void => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      if (entry.name === '__pycache__') {
        fs.rmSync(path.join(dir, entry.name), { recursive: true, force: true });
      } else {
        dropCaches(path.join(dir, entry.name));
      }
    }
  };
  dropCaches(outputDir);
  return true;
}

/**
 * One code bundle, two functions:
 *  - pipeline: Lambda durable function running the seven scan steps, invoked through the `live` alias.
 *  - api: plain function behind the HTTP API (see ApiStack).
 */
export class PipelineStack extends cdk.Stack {
  public readonly pipelineFunction: lambda.Function;
  public readonly pipelineAlias: lambda.Alias;
  public readonly apiFunction: lambda.Function;

  constructor(scope: Construct, id: string, props: PipelineStackProps) {
    super(scope, id, props);

    const runObjects = props.bucket.arnForObjects('runs/*');

    const stageDir = stageBackend();
    const code = lambda.Code.fromAsset(stageDir, {
      bundling: {
        image: PYTHON_RUNTIME.bundlingImage,
        platform: 'linux/arm64',
        command: ['bash', '-c', 'pip install --quiet --no-compile -r requirements.txt -t /asset-output && cp -r s3dp /asset-output/'],
        local: { tryBundle: (outputDir: string) => bundleBackendLocally(stageDir, outputDir) },
      },
    });

    const environment: Record<string, string> = {
      REPORTS_BUCKET: props.bucket.bucketName,
      RUNS_TABLE: props.table.tableName,
      MULTI_ACCOUNT: props.multiAccount ? 'true' : 'false',
    };

    // ---- durable pipeline function ----
    const pipelineLogGroup = new logs.LogGroup(this, 'PipelineLogGroup', {
      logGroupName: `/aws/lambda/${PIPELINE_FUNCTION_NAME}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Fixed name: the spoke roles in member accounts trust exactly this role (aws:PrincipalArn).
    const pipelineRole = new iam.Role(this, 'PipelineRole', {
      roleName: hubRoleName(this.region),
      description: 'S3 data protection advisor: hub scan pipeline',
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicDurableExecutionRolePolicy'),
      ],
    });

    this.pipelineFunction = new lambda.Function(this, 'PipelineFunction', {
      functionName: PIPELINE_FUNCTION_NAME,
      description: 'S3 data protection advisor: seven-step scan (Lambda durable function)',
      runtime: PYTHON_RUNTIME,
      architecture: lambda.Architecture.ARM_64,
      handler: 's3dp.service.pipeline.handler',
      code,
      role: pipelineRole,
      memorySize: 1024,
      timeout: cdk.Duration.minutes(15),
      logGroup: pipelineLogGroup,
      environment: {
        ...environment,
        MODEL_ID: props.modelId,
        SPOKE_ROLE_NAME,
        SCAN_ACCOUNTS: props.scanAccounts.join(','),
      },
      durableConfig: {
        // An organization scan visits every member account: allow it far longer than one account needs.
        executionTimeout: cdk.Duration.hours(props.multiAccount ? 8 : 2),
        retentionPeriod: cdk.Duration.days(14),
      },
    });
    this.pipelineFunction.addToRolePolicy(new iam.PolicyStatement({
      sid: 'ScanReadOnly',
      actions: [...SCAN_ACTIONS],
      resources: ['*'], // read-only describe/list calls without resource-level permissions
    }));
    if (props.multiAccount) {
      this.pipelineFunction.addToRolePolicy(new iam.PolicyStatement({
        sid: 'OrganizationAccounts',
        actions: [...ORG_ACTIONS],
        resources: ['*'], // DescribeOrganization and ListAccounts have no resource-level permissions
      }));
      // Any account's spoke role; each member account's trust policy decides whether the hub may assume it.
      this.pipelineFunction.addToRolePolicy(new iam.PolicyStatement({
        sid: 'AssumeSpokeRole',
        actions: ['sts:AssumeRole'],
        resources: [`arn:${this.partition}:iam::*:role/${SPOKE_ROLE_NAME}`],
      }));
    }
    if (props.modelId) {
      this.pipelineFunction.addToRolePolicy(new iam.PolicyStatement({
        sid: 'BedrockNovaLite',
        actions: [...BEDROCK_ACTIONS],
        resources: [
          `arn:${this.partition}:bedrock:${this.region}:${this.account}:inference-profile/${props.modelId}`,
          `arn:${this.partition}:bedrock:*::foundation-model/${NOVA_LITE_FOUNDATION_MODEL}`,
        ],
      }));
    }
    this.pipelineFunction.addToRolePolicy(new iam.PolicyStatement({
      sid: 'RunObjects',
      actions: ['s3:GetObject', 's3:PutObject'],
      resources: [runObjects],
    }));
    // HeadObject on a missing key answers 404 only with ListBucket (403 otherwise)
    this.pipelineFunction.addToRolePolicy(new iam.PolicyStatement({
      sid: 'RunObjectsHead',
      actions: ['s3:ListBucket'],
      resources: [props.bucket.bucketArn],
    }));
    this.pipelineFunction.addToRolePolicy(new iam.PolicyStatement({
      sid: 'RunsTable',
      actions: ['dynamodb:UpdateItem', 'dynamodb:GetItem'],
      resources: [props.table.tableArn],
    }));

    // Durable functions must be invoked through a qualified ARN.
    this.pipelineAlias = new lambda.Alias(this, 'PipelineLiveAlias', {
      aliasName: 'live',
      version: this.pipelineFunction.currentVersion,
    });

    // ---- plain API function ----
    const apiLogGroup = new logs.LogGroup(this, 'ApiLogGroup', {
      logGroupName: `/aws/lambda/${API_FUNCTION_NAME}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    this.apiFunction = new lambda.Function(this, 'ApiFunction', {
      functionName: API_FUNCTION_NAME,
      description: 'S3 data protection advisor: HTTP API (starts and observes scans, serves reports)',
      runtime: PYTHON_RUNTIME,
      architecture: lambda.Architecture.ARM_64,
      handler: 's3dp.service.api.handler',
      code,
      memorySize: 256,
      timeout: cdk.Duration.seconds(30),
      logGroup: apiLogGroup,
      environment: { ...environment, PIPELINE_FUNCTION_ARN: this.pipelineAlias.functionArn },
    });
    this.apiFunction.addToRolePolicy(new iam.PolicyStatement({
      sid: 'StartScan',
      actions: ['lambda:InvokeFunction'],
      resources: [this.pipelineAlias.functionArn],
    }));
    // Scope to the unqualified function ARN and ":*": a durable execution ARN carries the resolved numeric
    // version (...:fn:5/durable-execution/...), which an alias-scoped resource does not match (same as the
    // reference demo operations-automation/ai-lambda-runtime-migration).
    this.apiFunction.addToRolePolicy(new iam.PolicyStatement({
      sid: 'ObserveScans',
      actions: [
        'lambda:ListDurableExecutionsByFunction',
        'lambda:GetDurableExecution',
        'lambda:GetDurableExecutionHistory',
      ],
      resources: [this.pipelineFunction.functionArn, `${this.pipelineFunction.functionArn}:*`],
    }));
    this.apiFunction.addToRolePolicy(new iam.PolicyStatement({
      sid: 'ReportObjects',
      actions: ['s3:GetObject'],
      resources: [runObjects],
    }));
    this.apiFunction.addToRolePolicy(new iam.PolicyStatement({
      sid: 'ReportObjectsHead',
      actions: ['s3:ListBucket'],
      resources: [props.bucket.bucketArn],
    }));
    this.apiFunction.addToRolePolicy(new iam.PolicyStatement({
      sid: 'RunsTable',
      actions: ['dynamodb:UpdateItem', 'dynamodb:GetItem', 'dynamodb:Scan'],
      resources: [props.table.tableArn],
    }));

    new cdk.CfnOutput(this, 'PipelineFunctionArn', {
      value: this.pipelineAlias.functionArn,
      description: 'Qualified ARN of the durable scan pipeline',
    });
  }
}
