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
import { NodejsFunction } from 'aws-cdk-lib/aws-lambda-nodejs';
import { Runtime, Function as LambdaFunction } from 'aws-cdk-lib/aws-lambda';
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
  public readonly eventRouter: NodejsFunction;

  constructor(scope: Construct, id: string, props: EventIngestionProps) {
    super(scope, id);

    // Environment-aware log retention: 90 days production, 14 days non-production
    const logRetention = props.deployEnvironment === 'production'
      ? logs.RetentionDays.THREE_MONTHS
      : logs.RetentionDays.TWO_WEEKS;

    // Event Router Lambda — parses Health events and starts the workflow
    this.eventRouter = new NodejsFunction(this, 'EventRouter', {
      runtime: Runtime.NODEJS_24_X,
      entry: path.join(__dirname, '../../lambda/event-router/index.ts'),
      handler: 'handler',
      timeout: cdk.Duration.seconds(30),
      memorySize: 256,
      environment: {
        STATE_MACHINE_ARN: props.stateMachine.stateMachineArn,
      },
      logGroup: new logs.LogGroup(this, 'EventRouterLogs', {
        retention: logRetention,
        removalPolicy: cdk.RemovalPolicy.DESTROY,
      }),
      description: 'Routes AWS Health events to the investigation workflow',
      bundling: {
        forceDockerBundling: false,
        externalModules: ['@aws-sdk/*'],
      },
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

    // Additional rule for scheduled changes specifically
    const scheduledChangeRule = new events.Rule(this, 'ScheduledChangeRule', {
      ruleName: 'health-event-analyzer-scheduled',
      description: 'Captures scheduled AWS Health maintenance events',
      eventPattern: {
        source: ['aws.health'],
        detailType: ['AWS Health Event'],
        detail: {
          eventTypeCategory: ['scheduledChange'],
        },
      },
    });

    scheduledChangeRule.addTarget(new targets.LambdaFunction(this.eventRouter, {
      retryAttempts: 185,
      maxEventAge: cdk.Duration.hours(24),
    }));
  }
}
