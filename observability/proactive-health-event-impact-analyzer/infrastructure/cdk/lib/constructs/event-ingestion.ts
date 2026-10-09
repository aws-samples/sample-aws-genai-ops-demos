import * as cdk from 'aws-cdk-lib';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as lambdaDestinations from 'aws-cdk-lib/aws-lambda-destinations';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cloudwatch_actions from 'aws-cdk-lib/aws-cloudwatch-actions';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import { Runtime } from 'aws-cdk-lib/aws-lambda';
import { Construct } from 'constructs';
import * as path from 'path';

export interface EventIngestionProps {
  stateMachine: sfn.IStateMachine;
  /** Deployment environment — drives log retention (production=90d, non-production=14d) */
  deployEnvironment: string;
  /** SNS topic for DLQ alarm notifications */
  alarmTopic: sns.ITopic;
}

export class EventIngestion extends Construct {
  public readonly eventRouter: lambda.Function;

  constructor(scope: Construct, id: string, props: EventIngestionProps) {
    super(scope, id);

    // Environment-aware log retention: 90 days production, 14 days non-production
    const logRetention = props.deployEnvironment === 'production'
      ? logs.RetentionDays.THREE_MONTHS
      : logs.RetentionDays.TWO_WEEKS;

    const isProduction = props.deployEnvironment === 'production';

    // ─── DynamoDB: Security Hub finding dedup table ───────────────────────────
    // Dedicated table written ONLY by the Event Router. One item per finding Id
    // with a 6h TTL, so a given finding starts at most one investigation per
    // window even across concurrent/duplicate EventBridge deliveries. Mirrors
    // health-analyzer-task-tokens: PAY_PER_REQUEST, TTL on `ttl`, PITR on,
    // deletion protection + RETAIN in production and disposable elsewhere.
    const findingDedupTable = new dynamodb.Table(this, 'SecurityHubFindingDedupTable', {
      tableName: 'health-analyzer-securityhub-dedup',
      partitionKey: { name: 'findingId', type: dynamodb.AttributeType.STRING },
      timeToLiveAttribute: 'ttl',
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      deletionProtection: isProduction,
      removalPolicy: isProduction ? cdk.RemovalPolicy.RETAIN : cdk.RemovalPolicy.DESTROY,
    });

    // Event Router Lambda — parses Health events and starts the workflow.
    //
    // Code is pre-bundled by `npm run bundle` (scripts/bundle-lambdas.js) rather
    // than by CDK's NodejsFunction: that construct's local esbuild path shells out
    // to `powershell.exe` on Windows and fails wherever it is not on PATH. Every
    // other demo in this repository uses lambda.Function + Code.fromAsset for the
    // same reason.
    this.eventRouter = new lambda.Function(this, 'EventRouter', {
      runtime: Runtime.NODEJS_24_X,
      code: lambda.Code.fromAsset(path.join(__dirname, '../../dist/lambda/event-router')),
      handler: 'index.handler',
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      environment: {
        STATE_MACHINE_ARN: props.stateMachine.stateMachineArn,
        FINDING_DEDUP_TABLE: findingDedupTable.tableName,
      },
      logGroup: new logs.LogGroup(this, 'EventRouterLogs', {
        retention: logRetention,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
      description: 'Routes AWS Health events to the investigation workflow',
    });

    // ─── Dead Letter Queue for Event Router async invocation failures ────────
    const eventRouterDlq = new sqs.Queue(this, 'EventRouterDlq', {
      queueName: 'health-analyzer-event-router-dlq',
      retentionPeriod: cdk.Duration.days(14),
    });

    // Configure Lambda async invocation: max retry attempts = 2, DLQ destination
    new lambda.EventInvokeConfig(this, 'EventRouterInvokeConfig', {
      function: this.eventRouter,
      maxEventAge: cdk.Duration.hours(6),
      retryAttempts: 2,
      onFailure: new lambdaDestinations.SqsDestination(eventRouterDlq),
    });

    // CloudWatch Alarm: trigger when messages appear in the DLQ
    const eventRouterDlqAlarm = new cloudwatch.Alarm(this, 'EventRouterDlqAlarm', {
      alarmName: 'health-analyzer-event-router-dlq-messages',
      alarmDescription: 'Event Router DLQ has messages — failed async invocations detected',
      metric: eventRouterDlq.metricApproximateNumberOfMessagesVisible({
        period: cdk.Duration.seconds(60),
      }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });
    eventRouterDlqAlarm.addAlarmAction(new cloudwatch_actions.SnsAction(props.alarmTopic));

    // Grant the router permission to start the state machine
    props.stateMachine.grantStartExecution(this.eventRouter);

    // Least privilege: the Router only ever conditionally PutItem's into the
    // dedup table (never reads/deletes). Granting the explicit action produces a
    // table-ARN-scoped statement (not a wildcard resource).
    findingDedupTable.grant(this.eventRouter, 'dynamodb:PutItem');

    // EventBridge rule for AWS Health events
    const healthEventRule = new events.Rule(this, 'HealthEventRule', {
      ruleName: 'health-event-analyzer-capture',
      description: 'Captures AWS Health events for impact analysis',
      eventPattern: {
        source: ['aws.health'],
        detailType: [
          'AWS Health Event',
          'AWS Health Abuse Event',
        ],
      },
    });

    healthEventRule.addTarget(new targets.LambdaFunction(this.eventRouter, {
      retryAttempts: 185,
      maxEventAge: cdk.Duration.hours(24),
    }));

    // ─── EventBridge rule for AWS Security Hub findings ───────────────────────
    // Targets the SAME Event Router Lambda as the Health rule. The three leaf
    // filters below (Severity.Label, Workflow.Status, RecordState) live under
    // the `findings[]` array, so EventBridge applies them with ANY-ELEMENT
    // semantics: a batch is admitted if ANY one finding satisfies them, and the
    // WHOLE batch (including non-qualifying findings) is delivered. This pattern
    // is therefore only a COARSE batch-admission gate for cost/noise reduction —
    // the AUTHORITATIVE per-finding gate is re-applied inside the Event Router
    // (see handleSecurityHubEvent). Net behavior: only NEW/ACTIVE/HIGH/CRITICAL
    // findings ever start an execution, enforced in the Lambda regardless.
    //
    // The `source` + `detail-type` are not array-nested and are always valid.
    // FR-1a fallback: if `cdk synth`/deploy ever rejects a nested leaf (e.g.
    // Workflow.Status), drop ONLY that leaf here and keep the rest — the Router
    // filter still enforces it, so net behavior is unchanged. aws.securityhub
    // and aws.health are disjoint sources, so this rule never overlaps the
    // Health rule.
    const securityHubFindingRule = new events.Rule(this, 'SecurityHubFindingRule', {
      ruleName: 'health-analyzer-securityhub-capture',
      description: 'Captures HIGH/CRITICAL AWS Security Hub findings for impact analysis',
      eventPattern: {
        source: ['aws.securityhub'],
        detailType: ['Security Hub Findings - Imported'],
        detail: {
          findings: {
            Severity: { Label: ['HIGH', 'CRITICAL'] },
            Workflow: { Status: ['NEW'] },
            RecordState: ['ACTIVE'],
          },
        },
      },
    });

    securityHubFindingRule.addTarget(new targets.LambdaFunction(this.eventRouter, {
      retryAttempts: 185,
      maxEventAge: cdk.Duration.hours(24),
    }));

    // Note: scheduledChange-category events are already captured by
    // HealthEventRule above (detailType 'AWS Health Event' with no
    // eventTypeCategory filter). A separate rule scoped to
    // detail.eventTypeCategory: ['scheduledChange'] would be a strict subset
    // of that pattern and, since it would target the same Event Router Lambda,
    // would deliver every scheduledChange event to the router twice — starting
    // two identical Step Functions executions per event. No such rule is
    // registered here to keep each Health event to a single investigation.
  }
}
