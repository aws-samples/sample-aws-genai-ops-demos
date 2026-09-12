import * as cdk from 'aws-cdk-lib';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as scheduler from 'aws-cdk-lib/aws-scheduler';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import { Construct } from 'constructs';
import { spawnSync } from 'child_process';
import * as fs from 'fs';
import * as path from 'path';

export interface PipelineStackProps extends cdk.StackProps {
  lifecycleTable: dynamodb.ITable;
  configTable: dynamodb.ITable;
  stateTable: dynamodb.ITable;
  inventoryTable: dynamodb.ITable;
  actionPlanTable: dynamodb.ITable;
}

const BACKEND_DIR = path.join(__dirname, '..', '..', 'backend');
const PIPELINE_FUNCTION_NAME = 'aws-services-lifecycle-pipeline';
const API_FUNCTION_NAME = 'aws-services-lifecycle-api';
// One place for the Lambda Python version: runtime of both functions, the
// Docker bundling image and the pip wheel target must agree.
const PYTHON_RUNTIME = lambda.Runtime.PYTHON_3_14;
const PYTHON_VERSION = '3.14';

// Repo-wide region/account helpers (shared/utils/aws_utils.py). The Lambda
// bundle cannot import from outside its own directory, so the file is copied
// into the staged backend at synth time: one source of truth, no local copy.
const SHARED_AWS_UTILS = path.join(__dirname, '..', '..', '..', '..', 'shared', 'utils', 'aws_utils.py');
const STAGE_DIR = path.join(__dirname, '..', '.backend-stage');

/**
 * Assemble the Lambda source tree: backend/*.py + requirements.txt plus the
 * shared aws_utils.py. Tests and caches are never staged.
 */
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

/**
 * Bundle the Python code without Docker: pip resolves Linux/arm64 wheels for
 * the Lambda runtime (all dependencies are pure Python), then the staged
 * sources are copied alongside. Returning false lets CDK fall back to the
 * Docker bundling image.
 */
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
 * Lifecycle refresh pipeline (issue #139).
 *
 * One code bundle, two Lambda functions:
 *  - pipeline: Lambda durable function running the whole refresh
 *    (extract -> scan -> reconcile -> notify) as one checkpointed execution.
 *    Invoked through the `live` alias (durable functions need a qualified ARN).
 *  - api: plain function behind API Gateway (see ApiStack) serving UI actions,
 *    starting/observing pipeline executions, and receiving the weekly
 *    schedule.
 * Replaces the Step Functions machine and the AgentCore runtime.
 */
export class PipelineStack extends cdk.Stack {
  public readonly pipelineFunction: lambda.Function;
  public readonly pipelineAlias: lambda.Alias;
  public readonly apiFunction: lambda.Function;
  public readonly notificationTopic: sns.Topic;

  constructor(scope: Construct, id: string, props: PipelineStackProps) {
    super(scope, id, props);

    this.notificationTopic = new sns.Topic(this, 'ExtractionNotifications', {
      topicName: 'aws-services-lifecycle-notifications',
      displayName: 'AWS Services Lifecycle Extraction Notifications',
    });

    const deadLetterQueue = new sqs.Queue(this, 'SchedulerDLQ', {
      queueName: 'aws-services-lifecycle-scheduler-dlq',
      retentionPeriod: cdk.Duration.days(14),
      enforceSSL: true,
    });

    // ------------------------------------------------------------------
    // Shared code bundle
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

    const tableEnvironment = {
      LIFECYCLE_TABLE_NAME: props.lifecycleTable.tableName,
      CONFIG_TABLE_NAME: props.configTable.tableName,
      STATE_TABLE_NAME: props.stateTable.tableName,
      INVENTORY_TABLE_NAME: props.inventoryTable.tableName,
      ACTION_PLAN_TABLE_NAME: props.actionPlanTable.tableName,
      NOTIFICATION_TOPIC_ARN: this.notificationTopic.topicArn,
    };

    // ------------------------------------------------------------------
    // Shared data-plane permissions (same boundary as before, issue #116):
    // full access to backend-owned tables, read + UpdateItem only on the
    // repo-owned configuration table (no Put/Delete/BatchWrite).
    // ------------------------------------------------------------------
    const dataAccessPolicy = new iam.ManagedPolicy(this, 'LifecycleDataAccess', {
      description: 'Lifecycle tracker Lambda access to DynamoDB, Bedrock, AWS Health and read-only discovery APIs',
      statements: [
        new iam.PolicyStatement({
          sid: 'DynamoDBAgentOwnedAccess',
          actions: [
            'dynamodb:GetItem', 'dynamodb:PutItem', 'dynamodb:UpdateItem', 'dynamodb:DeleteItem',
            'dynamodb:Query', 'dynamodb:Scan', 'dynamodb:BatchGetItem', 'dynamodb:BatchWriteItem',
          ],
          resources: [
            props.lifecycleTable, props.stateTable, props.inventoryTable,
            props.actionPlanTable,
          ].flatMap((table) => [table.tableArn, `${table.tableArn}/index/*`]),
        }),
        new iam.PolicyStatement({
          sid: 'DynamoDBConfigReadAndUpdate',
          actions: ['dynamodb:GetItem', 'dynamodb:UpdateItem', 'dynamodb:Query', 'dynamodb:Scan', 'dynamodb:BatchGetItem'],
          resources: [props.configTable.tableArn, `${props.configTable.tableArn}/index/*`],
        }),
        new iam.PolicyStatement({
          sid: 'BedrockModelInvocation',
          actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream', 'bedrock:Converse', 'bedrock:ConverseStream'],
          resources: [
            'arn:aws:bedrock:*::foundation-model/*',
            `arn:aws:bedrock:*:${this.account}:inference-profile/*`,
          ],
        }),
        new iam.PolicyStatement({
          // Scan-time cross-check: which inventory ARNs appear in open planned
          // lifecycle notices (#141). Needs Business/Enterprise Support at runtime.
          sid: 'HealthAPIAccess',
          actions: ['health:DescribeEvents', 'health:DescribeAffectedEntities'],
          resources: ['*'], // Health API has no resource-level permissions
        }),
        new iam.PolicyStatement({
          sid: 'AccountResourceDiscovery',
          actions: [
            'lambda:ListFunctions',
            'rds:DescribeDBInstances', 'rds:DescribeDBClusters',
            'eks:ListClusters', 'eks:DescribeCluster',
            'elasticache:DescribeCacheClusters',
            'es:ListDomainNames', 'es:DescribeDomain',
            'kafka:ListClustersV2',
            'neptune:DescribeDBClusters',
            'glue:GetJobs',
            'elasticbeanstalk:DescribeEnvironments',
            'ec2:DescribeInstances',
          ],
          resources: ['*'], // List/Describe calls: read-only, no resource scoping available
        }),
        new iam.PolicyStatement({
          sid: 'PublishNotifications',
          actions: ['sns:Publish'],
          resources: [this.notificationTopic.topicArn],
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

    this.pipelineFunction = new lambda.Function(this, 'PipelineFunction', {
      functionName: PIPELINE_FUNCTION_NAME,
      description: 'Lifecycle refresh pipeline: extract -> scan -> reconcile -> notify (Lambda durable function)',
      runtime: PYTHON_RUNTIME,
      architecture: lambda.Architecture.ARM_64,
      handler: 'pipeline.handler',
      code: backendCode,
      memorySize: 1024,
      timeout: cdk.Duration.minutes(15),
      logGroup: pipelineLogGroup,
      environment: tableEnvironment,
      durableConfig: {
        executionTimeout: cdk.Duration.hours(2),
        retentionPeriod: cdk.Duration.days(14),
      },
    });
    this.pipelineFunction.role!.addManagedPolicy(dataAccessPolicy);
    this.pipelineFunction.role!.addManagedPolicy(
      iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicDurableExecutionRolePolicy'),
    );

    // Durable functions must be invoked through a qualified ARN.
    this.pipelineAlias = new lambda.Alias(this, 'PipelineLiveAlias', {
      aliasName: 'live',
      version: this.pipelineFunction.currentVersion,
    });

    // ------------------------------------------------------------------
    // Plain API function (UI actions, pipeline control, weekly schedule)
    // ------------------------------------------------------------------
    const apiLogGroup = new logs.LogGroup(this, 'ApiLogGroup', {
      logGroupName: `/aws/lambda/${API_FUNCTION_NAME}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    this.apiFunction = new lambda.Function(this, 'ApiFunction', {
      functionName: API_FUNCTION_NAME,
      description: 'Lifecycle tracker API: UI actions, refresh pipeline control, weekly schedule entry point',
      runtime: PYTHON_RUNTIME,
      architecture: lambda.Architecture.ARM_64,
      handler: 'api.handler',
      code: backendCode,
      memorySize: 512,
      timeout: cdk.Duration.minutes(5),
      logGroup: apiLogGroup,
      environment: {
        ...tableEnvironment,
        PIPELINE_FUNCTION_ARN: this.pipelineAlias.functionArn,
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

    // ------------------------------------------------------------------
    // Schedules (EventBridge Scheduler -> API function)
    // Both go through the API function: the weekly run reuses the same
    // naming / adopt-running logic as a UI-triggered refresh.
    // ------------------------------------------------------------------
    const schedulerRole = new iam.Role(this, 'SchedulerRole', {
      assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com'),
      description: 'EventBridge Scheduler role invoking the lifecycle tracker API function',
    });
    this.apiFunction.grantInvoke(schedulerRole);
    deadLetterQueue.grantSendMessages(schedulerRole);

    const scheduleTarget = (payload: Record<string, string>): scheduler.CfnSchedule.TargetProperty => ({
      arn: this.apiFunction.functionArn,
      roleArn: schedulerRole.roleArn,
      input: JSON.stringify(payload),
      retryPolicy: { maximumEventAgeInSeconds: 3600, maximumRetryAttempts: 0 },
      deadLetterConfig: { arn: deadLetterQueue.queueArn },
    });

    const weeklySchedule = new scheduler.CfnSchedule(this, 'WeeklyRefreshSchedule', {
      name: 'aws-services-lifecycle-weekly-refresh',
      description: 'Weekly end-to-end lifecycle refresh (durable pipeline)',
      scheduleExpression: 'rate(7 days)',
      scheduleExpressionTimezone: 'UTC',
      flexibleTimeWindow: { mode: 'OFF' },
      target: scheduleTarget({ action: 'start_refresh', refresh_origin: 'Auto' }),
    });

    // ------------------------------------------------------------------
    // Outputs
    // ------------------------------------------------------------------
    new cdk.CfnOutput(this, 'PipelineFunctionAliasArn', {
      value: this.pipelineAlias.functionArn,
      description: 'Qualified ARN of the durable refresh pipeline (invoke with --durable-execution-name)',
    });
    new cdk.CfnOutput(this, 'ApiFunctionArn', {
      value: this.apiFunction.functionArn,
      description: 'ARN of the API function (UI actions, pipeline control, weekly schedule)',
    });
    new cdk.CfnOutput(this, 'NotificationTopicArn', {
      value: this.notificationTopic.topicArn,
      description: 'SNS topic receiving refresh completion summaries',
    });
    new cdk.CfnOutput(this, 'WeeklyScheduleName', { value: weeklySchedule.name! });
    new cdk.CfnOutput(this, 'DeadLetterQueueUrl', { value: deadLetterQueue.queueUrl });
  }
}
