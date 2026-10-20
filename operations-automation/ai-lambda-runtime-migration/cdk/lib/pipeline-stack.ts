import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import { Construct } from 'constructs';
import { DISCOVER_READ_ACTIONS, hubPipelineRoleName, SPOKE_ROLE_NAME } from './scan-permissions';
import { spawnSync } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';

export interface PipelineStackProps extends cdk.StackProps {
  table: dynamodb.ITable;
  bucketName: string;
  /** Still accepted from app.ts, but no longer gates anything in this stack:
   *  multi-account vs single-account is decided at runtime (org_accounts.py),
   *  and the org-read + spoke-assume grants are attached unconditionally. */
  multiAccount?: boolean;
  /** Fixed name of the read-only spoke role (opt-in multi-account scope, Phase 2 rollout). */
  spokeRoleName?: string;
  /** Optional sts:ExternalId presented when assuming spoke roles. */
  spokeExternalId?: string;
  /** Opt-in Phase-2 migration gate (mirrors app.ts `transform` context). When
   *  set, the migration orchestrator (migrate.handler) and the build_complete
   *  handler are created in this stack and wired to the API function
   *  intra-stack; TransformStack (separate, opt-in) owns the build plane. */
  transform?: boolean;
}

const BACKEND_DIR = path.join(__dirname, '..', '..', 'backend');
const PIPELINE_FUNCTION_NAME = 'lambda-runtime-migration-pipeline';
const API_FUNCTION_NAME = 'lambda-runtime-migration-api';
// Phase-2 migration functions (created only under the `transform` gate).
const MIGRATE_FUNCTION_NAME = 'lambda-runtime-migration-migrate';
const BUILD_COMPLETE_FUNCTION_NAME = 'lambda-runtime-migration-build-complete';
// Fixture repository name (fixed in mock-fixture-stack.ts); the orchestrator's
// verify reads are scoped to it.
const FIXTURE_REPO_NAME = 'lambda-migration-demo-fixture';
// One place for the Lambda Python version: runtime of both functions, the
// Docker bundling image and the pip wheel target must agree.
const PYTHON_RUNTIME = lambda.Runtime.PYTHON_3_13;
const PYTHON_VERSION = '3.13';

// Repo-wide region/model-id helpers (shared/utils/aws_utils.py). The Lambda
// bundle cannot import from outside its own directory, so the file is copied
// into the staged backend at synth as aws_utils.py: one source of truth, no
// local copy and no sys.path hack. backend/model_id.py imports it at init.
const SHARED_AWS_UTILS = path.join(__dirname, '..', '..', '..', '..', 'shared', 'utils', 'aws_utils.py');
const STAGE_DIR = path.join(__dirname, '..', '.backend-stage');

/** Assemble the Lambda source tree: backend/*.py + requirements.txt plus the
 *  shared aws_utils.py. Tests and caches are never staged. */
function stageBackend(): string {
  fs.rmSync(STAGE_DIR, { recursive: true, force: true });
  fs.mkdirSync(STAGE_DIR, { recursive: true });
  for (const file of fs.readdirSync(BACKEND_DIR)) {
    if (file.endsWith('.py') || file === 'requirements.txt') {
      fs.copyFileSync(path.join(BACKEND_DIR, file), path.join(STAGE_DIR, file));
    }
  }
  fs.copyFileSync(SHARED_AWS_UTILS, path.join(STAGE_DIR, 'aws_utils.py'));
  return STAGE_DIR;
}

/** Bundle the Python code without Docker: pip resolves Linux/arm64 wheels for
 *  the Lambda runtime (all dependencies are pure Python), then the staged
 *  sources are copied alongside. Returning false lets CDK fall back to the
 *  Docker bundling image. */
function bundleBackendLocally(stageDir: string, outputDir: string): boolean {
  const pipArgs = [
    '-m', 'pip', 'install', '--quiet', '--disable-pip-version-check',
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
  for (const file of fs.readdirSync(stageDir)) {
    if (file.endsWith('.py')) {
      fs.copyFileSync(path.join(stageDir, file), path.join(outputDir, file));
    }
  }
  return true;
}

/**
 * Discover + Assess pipeline (v1.1 Phase 1).
 *
 * One code bundle, two Lambda functions:
 *  - pipeline: Lambda durable function running the whole Refresh (discover ->
 *    assess) as one checkpointed execution. Invoked through the `live` alias
 *    (durable functions need a qualified ARN).
 *  - api: plain function behind the HTTP API (see ApiStack) serving UI actions
 *    and starting/observing pipeline executions.
 * Replaces the three agent runtimes.
 */
export class PipelineStack extends cdk.Stack {
  public readonly pipelineFunction: lambda.Function;
  public readonly pipelineAlias: lambda.Alias;
  public readonly apiFunction: lambda.Function;
  // Phase-2 migration functions, populated only under the `transform` gate so
  // app.ts can pass them to TransformStack. Undefined on a default synth.
  public readonly migrateFunction?: lambda.Function;
  public readonly migrateAlias?: lambda.Alias;
  public readonly buildCompleteFunction?: lambda.Function;

  constructor(scope: Construct, id: string, props: PipelineStackProps) {
    super(scope, id, props);

    const spokeRoleName = props.spokeRoleName || SPOKE_ROLE_NAME;
    const bucketArn = `arn:${this.partition}:s3:::${props.bucketName}`;

    // ------------------------------------------------------------------
    // Shared code bundle (both functions share it; handlers differ)
    // ------------------------------------------------------------------
    const stageDir = stageBackend();
    const backendCode = lambda.Code.fromAsset(stageDir, {
      bundling: {
        image: PYTHON_RUNTIME.bundlingImage,
        platform: 'linux/arm64',
        command: [
          'bash', '-c',
          'pip install --quiet -r requirements.txt -t /asset-output && cp *.py /asset-output/',
        ],
        local: { tryBundle: (outputDir: string) => bundleBackendLocally(stageDir, outputDir) },
      },
    });

    const sharedEnvironment: Record<string, string> = {
      TABLE_NAME: props.table.tableName,
      BUCKET_NAME: props.bucketName,
      SPOKE_ROLE_NAME: spokeRoleName,
      ...(props.spokeExternalId ? { SPOKE_EXTERNAL_ID: props.spokeExternalId } : {}),
      // Multi-account vs single-account is decided at RUNTIME (design §5): the
      // pipeline always attempts organizations:ListAccounts and degrades to the
      // hub account alone when it cannot read an org. There is no MULTI_ACCOUNT
      // gate env var — a plain deploy already fans out to whatever org it can
      // read, which is what keeps the scan durable across redeploys.
      // MODEL_ID is NOT set here: there is no TypeScript getBedrockModelId, so
      // the Lambda computes it at init from the staged aws_utils.py (model_id.py)
      // with the unprefixed Nova base id. Set MODEL_ID only to override.
    };

    // ------------------------------------------------------------------
    // Shared data-plane permissions (least-privilege, design §2.1)
    // ------------------------------------------------------------------
    const dataAccessPolicy = new iam.ManagedPolicy(this, 'MigrationDataAccess', {
      description: 'Lambda runtime migration pipeline access: DynamoDB, Bedrock, and read-only discovery APIs',
      statements: [
        new iam.PolicyStatement({
          sid: 'DynamoDBInventoryAccess',
          // DeleteItem: discover re-keys a row whose key shape changed (writes the
          // new key, deletes the old one) and drops legacy duplicates of one
          // function. Without it the re-key half-applies and the scope fails.
          actions: [
            'dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:UpdateItem',
            'dynamodb:DeleteItem', 'dynamodb:Query', 'dynamodb:Scan',
          ],
          resources: [props.table.tableArn, `${props.table.tableArn}/index/*`],
        }),
        // No S3 here: the scan (Discover + Assess) and the API never touch the
        // bucket. Only the Phase-2 functions do, each with its own scoped
        // statement below (migrate writes the Transform config, build-complete
        // reads result.json).
        new iam.PolicyStatement({
          sid: 'BedrockModelInvocation',
          actions: ['bedrock:InvokeModel', 'bedrock:Converse'],
          resources: [
            'arn:aws:bedrock:*::foundation-model/*',
            `arn:aws:bedrock:*:${this.account}:inference-profile/*`,
          ],
        }),
        new iam.PolicyStatement({
          sid: 'DiscoverReadAccess',
          actions: DISCOVER_READ_ACTIONS,
          resources: ['*'], // Trusted Advisor / Lambda read / CloudWatch: no resource scoping
        }),
      ],
    });

    // ------------------------------------------------------------------
    // Durable pipeline function
    // ------------------------------------------------------------------
    const pipelineLogGroup = new logs.LogGroup(this, 'PipelineLogGroup', {
      logGroupName: `/aws/lambda/${PIPELINE_FUNCTION_NAME}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    // Fixed, region-suffixed role name so spoke trust policies can pin the
    // exact principal (design §2.1 / §5).
    const pipelineRole = new iam.Role(this, 'PipelineRole', {
      roleName: hubPipelineRoleName(this.region),
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      description: 'Lambda runtime migration pipeline role (hub): scans this account and assumes spoke roles',
    });

    this.pipelineFunction = new lambda.Function(this, 'PipelineFunction', {
      functionName: PIPELINE_FUNCTION_NAME,
      role: pipelineRole,
      description: 'Lambda runtime migration pipeline: discover -> assess (Lambda durable function)',
      runtime: PYTHON_RUNTIME,
      architecture: lambda.Architecture.ARM_64,
      handler: 'pipeline.handler',
      code: backendCode,
      memorySize: 1024,
      timeout: cdk.Duration.minutes(15),
      logGroup: pipelineLogGroup,
      environment: sharedEnvironment,
      durableConfig: {
        // Ceiling; a Phase-1 discover+assess run is minutes.
        executionTimeout: cdk.Duration.minutes(30),
        retentionPeriod: cdk.Duration.days(14),
      },
    });
    this.pipelineFunction.role!.addManagedPolicy(dataAccessPolicy);
    this.pipelineFunction.role!.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicDurableExecutionRolePolicy'),
    );
    // Multi-account grants are attached UNCONDITIONALLY (design §5): assume the
    // fixed-name read-only spoke role in member accounts and read account lists
    // from Organizations. These are decided at runtime (org_accounts.py always
    // attempts ListAccounts and degrades to the hub when it cannot read an org),
    // so a plain single-account deploy already carries them. They are harmless
    // there — no org to list, no spoke roles to assume — and keeping them off
    // the multiAccount flag is what makes the scan durable across redeploys.
    this.pipelineFunction.addToRolePolicy(new iam.PolicyStatement({
      sid: 'HubAndSpokeScan',
      actions: ['sts:AssumeRole'],
      resources: [`arn:${this.partition}:iam::*:role/${spokeRoleName}`],
    }));
    this.pipelineFunction.addToRolePolicy(new iam.PolicyStatement({
      sid: 'OrganizationsRead',
      actions: [
        'organizations:DescribeOrganization', 'organizations:ListAccounts', 'organizations:DescribeAccount',
        'organizations:ListRoots', 'organizations:ListOrganizationalUnitsForParent', 'organizations:ListParents',
        'organizations:DescribeOrganizationalUnit', 'organizations:ListAccountsForParent',
      ],
      resources: ['*'],
    }));

    // Durable functions must be invoked through a qualified ARN.
    this.pipelineAlias = new lambda.Alias(this, 'PipelineLiveAlias', {
      aliasName: 'live',
      version: this.pipelineFunction.currentVersion,
    });

    // ------------------------------------------------------------------
    // Phase-2 migration functions (opt-in `transform` gate)
    //
    // Co-located here, with the API function, so the API -> orchestrator wiring
    // (MIGRATE_FUNCTION_ARN + invoke grant) is entirely intra-stack and does not
    // form a CloudFormation dependency cycle with TransformStack. TransformStack
    // owns only the build plane and depends on these one-directionally.
    // ------------------------------------------------------------------
    let migrateAliasForApi: lambda.Alias | undefined;
    if (props.transform) {
      const migrateLogGroup = new logs.LogGroup(this, 'MigrateLogGroup', {
        logGroupName: `/aws/lambda/${MIGRATE_FUNCTION_NAME}`,
        retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      });

      const migrateFunction = new lambda.Function(this, 'MigrateFunction', {
        functionName: MIGRATE_FUNCTION_NAME,
        description: 'Lambda runtime migration orchestrator: resolve, AWS Transform via CodeBuild (durable callback), upgrade branch pushed by the build (Lambda durable function)',
        runtime: PYTHON_RUNTIME,
        architecture: lambda.Architecture.ARM_64,
        handler: 'migrate.handler',
        code: backendCode,
        memorySize: 512,
        timeout: cdk.Duration.minutes(5),
        logGroup: migrateLogGroup,
        environment: sharedEnvironment,
        durableConfig: {
          // The execution now WAITS for every CodeBuild run (durable callback),
          // so the ceiling must cover the pathological batch where each build in
          // each wave sits its full queue bound and then runs to its build
          // bound. Inputs are the constants in backend/migrate.py
          // (QUEUE_TIMEOUT_MINUTES, BUILD_TIMEOUT_MINUTES, CALLBACK_SLACK_MINUTES,
          // MAX_FUNCTIONS_PER_RUN, MIGRATE_CONCURRENCY); change one, re-derive this:
          //
          //   waves    = ceil(MAX_FUNCTIONS_PER_RUN / MIGRATE_CONCURRENCY)            = ceil(50 / 5) = 10
          //   per_wave = resolve retries (2) + QUEUE_TIMEOUT_MINUTES (10)
          //            + BUILD_TIMEOUT_MINUTES (60) + CALLBACK_SLACK_MINUTES (5)
          //            + settle step (1)                                              = 78 min
          //   ceiling  = waves x per_wave x 1.5 (replay latency, SDK retry backoff,
          //              EventBridge delay; queueing is already bounded above)        = 1170 min ~ 19.5 h -> 20 h
          //
          // A too-high ceiling costs nothing (an execution that finishes early
          // finishes early); a too-low one turns a slow but legitimate batch into
          // TIMED_OUT. A real fixture batch finishes in minutes.
          executionTimeout: cdk.Duration.hours(20),
          retentionPeriod: cdk.Duration.days(14),
        },
      });
      // Data-plane + verify grants for the orchestrator. Scan/Query are needed
      // because resolve_and_verify locates the function's fn# row via
      // api._scan_inventory (a DynamoDB Scan); GetItem/UpdateItem cover the mig#
      // read/writes. (index/* is included so a future GSI-backed lookup works.)
      migrateFunction.addToRolePolicy(new iam.PolicyStatement({
        sid: 'MigrationItemAccess',
        actions: ['dynamodb:GetItem', 'dynamodb:UpdateItem', 'dynamodb:Query', 'dynamodb:Scan'],
        resources: [props.table.tableArn, `${props.table.tableArn}/index/*`],
      }));
      // The orchestrator writes the AWS Transform config render
      // (migrations/<job_id>/transform-config.yaml) that the build reads back.
      // This is the config render, not a function-source upload.
      migrateFunction.addToRolePolicy(new iam.PolicyStatement({
        sid: 'MigrationArtifacts',
        actions: ['s3:GetObject', 's3:PutObject'],
        resources: [`${bucketArn}/*`],
      }));
      // Hub-region fixture repository only. The orchestrator calls CodeCommit in
      // the function's region (resolved['region']); a repo in any other region
      // is denied at resolve (ACCESS_DENIED) by design, this demo migrates the
      // fixture.
      const fixtureRepoArn = `arn:${this.partition}:codecommit:${this.region}:${this.account}:${FIXTURE_REPO_NAME}`;
      migrateFunction.addToRolePolicy(new iam.PolicyStatement({
        sid: 'MigrationRepoVerify',
        actions: ['codecommit:GetRepository', 'codecommit:GetBranch'],
        resources: [fixtureRepoArn],
      }));
      // No write grant on the repository: the build (TransformStack) pushes the
      // upgrade branch, and the flow ends there. The orchestrator only reads.
      migrateFunction.role!.addManagedPolicy(
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicDurableExecutionRolePolicy'),
      );

      // Durable functions must be invoked through a qualified ARN.
      const migrateAlias = new lambda.Alias(this, 'MigrateLiveAlias', {
        aliasName: 'live',
        version: migrateFunction.currentVersion,
      });

      const buildCompleteLogGroup = new logs.LogGroup(this, 'BuildCompleteLogGroup', {
        logGroupName: `/aws/lambda/${BUILD_COMPLETE_FUNCTION_NAME}`,
        retention: logs.RetentionDays.ONE_MONTH,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      });

      const buildCompleteFunction = new lambda.Function(this, 'BuildCompleteFunction', {
        functionName: BUILD_COMPLETE_FUNCTION_NAME,
        description: 'Lambda runtime migration completion handler: writes terminal migration status on a CodeBuild terminal-state event',
        runtime: PYTHON_RUNTIME,
        architecture: lambda.Architecture.ARM_64,
        handler: 'build_complete.handler',
        code: backendCode,
        memorySize: 256,
        timeout: cdk.Duration.minutes(2),
        logGroup: buildCompleteLogGroup,
        environment: sharedEnvironment,
      });
      buildCompleteFunction.addToRolePolicy(new iam.PolicyStatement({
        sid: 'MigrationItemAccess',
        actions: ['dynamodb:GetItem', 'dynamodb:UpdateItem'],
        resources: [props.table.tableArn],
      }));
      buildCompleteFunction.addToRolePolicy(new iam.PolicyStatement({
        sid: 'MigrationResultRead',
        actions: ['s3:GetObject'],
        resources: [`${bucketArn}/*`],
      }));
      // Completes the orchestrator's transform-<i> wait_for_callback with the
      // build result. The callback is authorized against the function that OWNS
      // the execution (the migrate function), not against build_complete, hence
      // the migrate function ARN here. Unqualified ARN + :* like MigrateDurableRead:
      // a durable execution ARN carries the resolved numeric version. Resource
      // shape checked with the IAM policy simulator (aws iam simulate-custom-policy):
      // the action on function:migrate and function:migrate:* evaluates allowed
      // for function:migrate and function:migrate:3, implicitDeny for
      // function:other, identically to lambda:GetDurableExecution. The managed
      // AWSLambdaBasicDurableExecutionRolePolicy carries no callback action, so
      // this statement is required. Success only: build_complete never sends
      // Failure (failures travel inside the success payload) and no heartbeat
      // timeout is configured.
      buildCompleteFunction.addToRolePolicy(new iam.PolicyStatement({
        sid: 'MigrationCallback',
        actions: ['lambda:SendDurableExecutionCallbackSuccess'],
        resources: [migrateFunction.functionArn, `${migrateFunction.functionArn}:*`],
      }));
      // basic Lambda logging is provided by the explicit log group + default
      // service permissions on the function role.

      this.migrateFunction = migrateFunction;
      this.migrateAlias = migrateAlias;
      this.buildCompleteFunction = buildCompleteFunction;
      migrateAliasForApi = migrateAlias;
    }

    // ------------------------------------------------------------------
    // Plain API function (UI actions + pipeline control)
    // ------------------------------------------------------------------
    const apiLogGroup = new logs.LogGroup(this, 'ApiLogGroup', {
      logGroupName: `/aws/lambda/${API_FUNCTION_NAME}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    this.apiFunction = new lambda.Function(this, 'ApiFunction', {
      functionName: API_FUNCTION_NAME,
      description: 'Lambda runtime migration API: UI actions and refresh pipeline control',
      runtime: PYTHON_RUNTIME,
      architecture: lambda.Architecture.ARM_64,
      handler: 'api.handler',
      code: backendCode,
      memorySize: 512,
      timeout: cdk.Duration.minutes(5),
      logGroup: apiLogGroup,
      environment: {
        ...sharedEnvironment,
        PIPELINE_FUNCTION_ARN: this.pipelineAlias.functionArn,
        // Phase-2: present only under the `transform` gate. api.py's
        // start_migration 503s when this is unset, so the two /migrate routes
        // exist unconditionally but only act when migration is deployed.
        ...(migrateAliasForApi ? { MIGRATE_FUNCTION_ARN: migrateAliasForApi.functionArn } : {}),
      },
    });
    this.apiFunction.role!.addManagedPolicy(dataAccessPolicy);
    this.apiFunction.addToRolePolicy(new iam.PolicyStatement({
      sid: 'PipelineControl',
      actions: [
        'lambda:InvokeFunction',
        'lambda:GetDurableExecution',
        'lambda:GetDurableExecutionHistory',
        'lambda:ListDurableExecutionsByFunction',
      ],
      resources: [this.pipelineFunction.functionArn, `${this.pipelineFunction.functionArn}:*`],
    }));
    this.apiFunction.addToRolePolicy(new iam.PolicyStatement({
      // Settings page: list the regions enabled for this account. API only.
      sid: 'RegionListing',
      actions: ['ec2:DescribeRegions'],
      resources: ['*'], // DescribeRegions has no resource-level permissions
    }));
    this.apiFunction.addToRolePolicy(new iam.PolicyStatement({
      // Resolve the hub account's friendly name for the inventory table
      // (the hub's rows carry an empty account_id; the API names it for display,
      // the same AWS Organizations source member accounts are named from).
      // Degrades gracefully to a placeholder name when the org can't be read.
      sid: 'HubAccountName',
      actions: ['organizations:DescribeAccount'],
      resources: ['*'], // DescribeAccount has no resource-level permissions
    }));

    // Phase-2: intra-stack invoke grant on the migration orchestrator alias —
    // same shape as PipelineControl, no cross-stack ref (no cycle). The API does
    // NOT get codebuild:StartBuild or codecommit:* (the orchestrator does those).
    if (migrateAliasForApi) {
      this.apiFunction.addToRolePolicy(new iam.PolicyStatement({
        sid: 'MigrateControl',
        actions: ['lambda:InvokeFunction'],
        resources: [migrateAliasForApi.functionArn, `${migrateAliasForApi.functionArn}:*`],
      }));
      // GET /migrate-run/{arn+} reads one known migrate execution's state and
      // history; GET /migrate-runs lists the function's executions newest-first
      // for the Steps card re-attach and the Activity log — the same
      // lambda:GetDurableExecution* / ListDurableExecutionsByFunction family
      // PipelineControl holds on the pipeline function.
      // Scope to the UNQUALIFIED function ARN (+ :*), like PipelineControl —
      // NOT the alias ARN. A durable execution ARN carries the resolved numeric
      // VERSION (…:migrate:5/durable-execution/…), so an alias-scoped resource
      // (…:migrate:live:*) does not match it and GetDurableExecution 500s with
      // AccessDenied. The unqualified-ARN + :* pattern covers every version.
      this.apiFunction.addToRolePolicy(new iam.PolicyStatement({
        sid: 'MigrateDurableRead',
        actions: [
          'lambda:GetDurableExecution',
          'lambda:GetDurableExecutionHistory',
          'lambda:ListDurableExecutionsByFunction',
        ],
        resources: [this.migrateFunction!.functionArn, `${this.migrateFunction!.functionArn}:*`],
      }));
    }

    // ------------------------------------------------------------------
    // Outputs
    // ------------------------------------------------------------------
    new cdk.CfnOutput(this, 'PipelineFunctionAliasArn', {
      value: this.pipelineAlias.functionArn,
      description: 'Qualified ARN of the durable discover+assess pipeline (invoke with --durable-execution-name)',
    });
    new cdk.CfnOutput(this, 'ApiFunctionArn', {
      value: this.apiFunction.functionArn,
      description: 'ARN of the API function (UI actions, pipeline control)',
    });
  }
}
