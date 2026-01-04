import * as cdk from 'aws-cdk-lib';
import * as scheduler from 'aws-cdk-lib/aws-scheduler';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import { Construct } from 'constructs';

export interface SchedulerStackProps extends cdk.StackProps {
  agentRuntimeArn: string;
}

export class AWSServicesLifecycleTrackerScheduler extends cdk.Stack {
  public readonly notificationTopic: sns.Topic;
  public readonly deadLetterQueue: sqs.Queue;

  constructor(scope: Construct, id: string, props: SchedulerStackProps) {
    super(scope, id, props);

    // Dead Letter Queue for failed scheduler invocations
    this.deadLetterQueue = new sqs.Queue(this, 'SchedulerDLQ', {
      queueName: 'scheduler-agentcore-dlq',
      retentionPeriod: cdk.Duration.days(14),
      visibilityTimeout: cdk.Duration.minutes(5)
    });

    // SNS Topic for notifications (optional)
    this.notificationTopic = new sns.Topic(this, 'ExtractionNotifications', {
      topicName: 'aws-services-lifecycle-notifications',
      displayName: 'AWS Services Lifecycle Extraction Notifications'
    });

    // Create IAM role for EventBridge Scheduler to invoke AgentCore
    const schedulerRole = new iam.Role(this, 'SchedulerAgentCoreRole', {
      assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com'),
      inlinePolicies: {
        InvokeAgentCore: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: [
                'bedrock-agentcore:InvokeAgentRuntime',
                'bedrock-agentcore:InvokeAgent'
              ],
              resources: [
                props.agentRuntimeArn,
                `${props.agentRuntimeArn}/*`
              ]
            }),
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: ['sqs:SendMessage'],
              resources: [this.deadLetterQueue.queueArn]
            })
          ]
        })
      }
    });

    // EventBridge Scheduler for AgentCore invocation
    const agentCoreSchedule = new scheduler.CfnSchedule(this, 'AgentCoreSchedule', {
      name: 'aws-services-lifecycle-weekly-extraction',
      description: 'Weekly extraction of AWS service lifecycle data',
      scheduleExpression: 'rate(7 days)',
      scheduleExpressionTimezone: 'UTC',
      flexibleTimeWindow: {
        mode: 'OFF'
      },
      target: {
        arn: `arn:aws:scheduler:::aws-sdk:bedrockagentcore:invokeAgentRuntime`,
        roleArn: schedulerRole.roleArn,
        input: JSON.stringify({
          AgentRuntimeArn: props.agentRuntimeArn,
          Payload: JSON.stringify({
            services: 'all',
            force_refresh: true,
            refresh_origin: 'Auto'
          })
        }),
        retryPolicy: {
          maximumEventAgeInSeconds: 86400,
          maximumRetryAttempts: 0
        },
        deadLetterConfig: {
          arn: this.deadLetterQueue.queueArn
        }
      }
    });

    // Outputs
    new cdk.CfnOutput(this, 'NotificationTopicArn', {
      value: this.notificationTopic.topicArn,
      description: 'ARN of the notification topic for extraction results'
    });

    new cdk.CfnOutput(this, 'WeeklySchedule', {
      value: 'Every 7 days - All enabled services with Auto refresh origin',
      description: 'Weekly extraction schedule for all services'
    });

    new cdk.CfnOutput(this, 'SchedulerRoleArn', {
      value: schedulerRole.roleArn,
      description: 'IAM role used by EventBridge Scheduler to invoke AgentCore'
    });

    new cdk.CfnOutput(this, 'ScheduleName', {
      value: agentCoreSchedule.name!,
      description: 'EventBridge Scheduler name for weekly extractions'
    });

    new cdk.CfnOutput(this, 'DeadLetterQueueUrl', {
      value: this.deadLetterQueue.queueUrl,
      description: 'SQS Dead Letter Queue for failed scheduler invocations'
    });
  }
}