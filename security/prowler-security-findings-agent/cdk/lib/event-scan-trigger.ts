import * as fs from 'fs';
import * as path from 'path';
import * as cdk from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as ecs from 'aws-cdk-lib/aws-ecs';
import * as events from 'aws-cdk-lib/aws-events';
import * as eventsTargets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import { SqsEventSource } from 'aws-cdk-lib/aws-lambda-event-sources';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import { Construct } from 'constructs';

const LAMBDA_DIR = path.join(__dirname, '..', 'lambda', 'event-scan-trigger');

export interface EventScanRule {
  id: string;
  group: string;
  source: string;
  eventNames?: string[];
  eventNamePrefixes?: string[];
  resolver: string;
  checks: string[];
  /** Hold the event this long before scanning, for changes AWS applies asynchronously. */
  delaySeconds?: number;
}

export function loadEventScanRules(): EventScanRule[] {
  return JSON.parse(fs.readFileSync(path.join(LAMBDA_DIR, 'rules.json'), 'utf-8')).rules;
}

export interface EventScanTriggerProps {
  cluster: ecs.ICluster;
  taskDefinition: ecs.FargateTaskDefinition;
  subnetIds: string[];
  securityGroup: ec2.ISecurityGroup;
  assignPublicIp: boolean;
  /** How long SQS collects events before the trigger Lambda coalesces them into scans. */
  batchWindow?: cdk.Duration;
}

/**
 * Event-driven targeted scans: a do-it-yourself take on Prowler Realtime.
 *
 * EventBridge rules (one per entry in lambda/event-scan-trigger/rules.json) match
 * risky "AWS API Call via CloudTrail" write events on the default bus and queue them
 * in SQS. The trigger Lambda reads each batching window, maps the calls to Prowler
 * checks and resource ARNs, and starts the scanner task definition with
 * SCAN_MODE=event container overrides. Requires a CloudTrail trail that logs
 * management events in the account.
 */
export class EventScanTrigger extends Construct {
  public readonly queue: sqs.Queue;
  public readonly deadLetterQueue: sqs.Queue;
  public readonly function: lambda.Function;

  constructor(scope: Construct, id: string, props: EventScanTriggerProps) {
    super(scope, id);

    const rules = loadEventScanRules();
    const batchWindow = props.batchWindow ?? cdk.Duration.seconds(60);
    const timeout = cdk.Duration.seconds(60);

    this.deadLetterQueue = new sqs.Queue(this, 'DeadLetterQueue', {
      queueName: 'prowler-security-event-scans-dlq',
      retentionPeriod: cdk.Duration.days(14),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      enforceSSL: true,
    });
    const queueProps: sqs.QueueProps = {
      // AWS recommends six times the function timeout plus the batching window.
      visibilityTimeout: cdk.Duration.seconds(timeout.toSeconds() * 6 + batchWindow.toSeconds()),
      retentionPeriod: cdk.Duration.days(1),
      encryption: sqs.QueueEncryption.SQS_MANAGED,
      enforceSSL: true,
      deadLetterQueue: { queue: this.deadLetterQueue, maxReceiveCount: 3 },
    };
    this.queue = new sqs.Queue(this, 'Queue', { ...queueProps, queueName: 'prowler-security-event-scans' });

    const delays = [...new Set(rules.map((r) => r.delaySeconds ?? 0).filter((d) => d > 0))];
    if (delays.length > 1) {
      throw new Error(`rules.json uses several delaySeconds values (${delays.join(', ')}); one delayed queue is supported`);
    }
    const delayedQueue = delays.length
      ? new sqs.Queue(this, 'DelayedQueue', {
        ...queueProps,
        queueName: 'prowler-security-event-scans-delayed',
        deliveryDelay: cdk.Duration.seconds(delays[0]),
      })
      : undefined;

    const logGroup = new logs.LogGroup(this, 'LogGroup', {
      logGroupName: '/aws/lambda/prowler-security-event-scan-trigger',
      retention: logs.RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
    });

    const role = new iam.Role(this, 'Role', {
      roleName: `prowler-security-event-scan-trigger-${cdk.Aws.REGION}`,
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      description: 'Starts targeted Prowler scans for risky CloudTrail write calls',
    });
    logGroup.grantWrite(role);
    role.addToPolicy(new iam.PolicyStatement({
      sid: 'RunScannerTask',
      actions: ['ecs:RunTask'],
      resources: [props.taskDefinition.taskDefinitionArn],
      conditions: { ArnEquals: { 'ecs:cluster': props.cluster.clusterArn } },
    }));
    role.addToPolicy(new iam.PolicyStatement({
      sid: 'PassScannerRoles',
      actions: ['iam:PassRole'],
      resources: [props.taskDefinition.taskRole.roleArn, props.taskDefinition.obtainExecutionRole().roleArn],
      conditions: { StringEquals: { 'iam:PassedToService': 'ecs-tasks.amazonaws.com' } },
    }));
    role.addToPolicy(new iam.PolicyStatement({
      sid: 'ResolveSecurityGroupVpc',
      // DescribeSecurityGroups supports no resource-level permissions.
      actions: ['ec2:DescribeSecurityGroups'],
      resources: ['*'],
    }));
    role.addToPolicy(new iam.PolicyStatement({
      sid: 'ResolveIamUserPath',
      actions: ['iam:GetUser'],
      resources: [`arn:${cdk.Aws.PARTITION}:iam::${cdk.Aws.ACCOUNT_ID}:user/*`],
    }));

    this.function = new lambda.Function(this, 'Function', {
      functionName: 'prowler-security-event-scan-trigger',
      description: 'Coalesces risky CloudTrail calls into targeted Prowler Fargate scans',
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(LAMBDA_DIR, { exclude: ['tests', '**/__pycache__', '*.pyc'] }),
      role,
      timeout,
      memorySize: 256,
      logGroup,
      environment: {
        CLUSTER_ARN: props.cluster.clusterArn,
        TASK_DEFINITION_ARN: props.taskDefinition.taskDefinitionArn,
        CONTAINER_NAME: props.taskDefinition.defaultContainer!.containerName,
        SUBNET_IDS: props.subnetIds.join(','),
        SECURITY_GROUP_ID: props.securityGroup.securityGroupId,
        ASSIGN_PUBLIC_IP: props.assignPublicIp ? 'ENABLED' : 'DISABLED',
      },
    });

    for (const queue of [this.queue, delayedQueue]) {
      if (!queue) continue;
      this.function.addEventSource(new SqsEventSource(queue, {
        batchSize: 100,
        maxBatchingWindow: batchWindow,
        reportBatchItemFailures: true,
        maxConcurrency: 2,
      }));
    }

    for (const rule of rules) {
      const eventName = [
        ...(rule.eventNames ?? []),
        ...(rule.eventNamePrefixes ?? []).map((prefix) => ({ prefix })),
      ];
      new events.Rule(this, `Rule-${rule.id}`, {
        ruleName: `prowler-security-event-scan-${rule.id}`,
        description: `Targeted Prowler scan (${rule.checks.length} checks) on ${rule.source} ${rule.id}`,
        eventPattern: {
          source: [rule.source],
          detailType: ['AWS API Call via CloudTrail'],
          detail: {
            eventName,
            errorCode: [{ exists: false }],
          },
        },
        targets: [new eventsTargets.SqsQueue((rule.delaySeconds ?? 0) > 0 ? delayedQueue! : this.queue)],
      });
    }
  }
}
