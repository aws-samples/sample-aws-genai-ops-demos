import * as cdk from 'aws-cdk-lib';
import * as scheduler from 'aws-cdk-lib/aws-scheduler';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import * as sfn from 'aws-cdk-lib/aws-stepfunctions';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
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

    // ------------------------------------------------------------------
    // Refresh All orchestration (issue #126)
    //
    // A Standard state machine fans out per-service extractions against the
    // AgentCore runtime (plain aws-sdk InvokeAgentRuntime integration — the
    // demo's agent is a custom runtime, not the managed harness). The fixed
    // name lets the Auth stack grant user-role IAM and the frontend receive
    // the ARN without cross-stack references (spec D2).
    // ------------------------------------------------------------------
    const stateMachineName = 'aws-services-lifecycle-refresh-all';
    const invokeAgentResource = 'arn:aws:states:::aws-sdk:bedrockagentcore:invokeAgentRuntime';

    // Role assumed by the state machine to invoke the agent runtime per service
    const refreshStateMachineRole = new iam.Role(this, 'RefreshAllStateMachineRole', {
      assumedBy: new iam.ServicePrincipal('states.amazonaws.com'),
      inlinePolicies: {
        InvokeAgentCore: new iam.PolicyDocument({
          statements: [
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: ['bedrock-agentcore:InvokeAgentRuntime'],
              resources: [props.agentRuntimeArn, `${props.agentRuntimeArn}/*`],
            }),
          ],
        }),
      },
    });

    // ASL definition (JSONata query language). Execution input:
    //   { "refresh_origin": "manual" | "Auto", "services"?: ["glue", ...] }
    // A provided services list wins (subset/single-service runs); otherwise
    // the agent's list_enabled_service_names action supplies all enabled ones.
    const refreshDefinition = {
      Comment:
        'Refresh All orchestration: fan out per-service lifecycle extractions against the AgentCore runtime (#126)',
      QueryLanguage: 'JSONata',
      StartAt: 'CheckProvidedServices',
      States: {
        CheckProvidedServices: {
          Type: 'Choice',
          Choices: [
            {
              Condition: '{% $exists($states.input.services) and $count($states.input.services) > 0 %}',
              Next: 'UseProvidedServices',
            },
          ],
          Default: 'ListEnabledServices',
        },
        UseProvidedServices: {
          Type: 'Pass',
          Output:
            "{% {'services': $states.input.services, 'refresh_origin': $exists($states.input.refresh_origin) ? $states.input.refresh_origin : 'manual'} %}",
          Next: 'RunExtractions',
        },
        ListEnabledServices: {
          Type: 'Task',
          Resource: invokeAgentResource,
          Arguments: {
            AgentRuntimeArn: props.agentRuntimeArn,
            ContentType: 'application/json',
            Accept: 'application/json',
            Payload: '{"action": "list_enabled_service_names"}',
          },
          Retry: [{ ErrorEquals: ['States.ALL'], IntervalSeconds: 5, MaxAttempts: 2, BackoffRate: 2 }],
          Output:
            "{% {'services': $parse($states.result.Response).services, 'refresh_origin': $exists($states.input.refresh_origin) ? $states.input.refresh_origin : 'manual'} %}",
          Next: 'RunExtractions',
        },
        RunExtractions: {
          Type: 'Map',
          Items: '{% $states.input.services %}',
          MaxConcurrency: 5,
          ItemSelector: {
            service: '{% $states.context.Map.Item.Value %}',
            origin: '{% $states.input.refresh_origin %}',
          },
          ItemProcessor: {
            ProcessorConfig: { Mode: 'INLINE' },
            StartAt: 'ExtractService',
            States: {
              ExtractService: {
                Type: 'Task',
                Resource: invokeAgentResource,
                Arguments: {
                  AgentRuntimeArn: props.agentRuntimeArn,
                  ContentType: 'application/json',
                  Accept: 'application/json',
                  Payload:
                    "{% $string({'service_name': $states.input.service, 'force_refresh': true, 'refresh_origin': $states.input.origin}) %}",
                },
                // Retries cover transport/service errors only; an extraction
                // that returns success:false is reported, not retried (a
                // failing docs page will not succeed on immediate retry).
                Retry: [{ ErrorEquals: ['States.ALL'], IntervalSeconds: 10, MaxAttempts: 2, BackoffRate: 2 }],
                Catch: [
                  {
                    ErrorEquals: ['States.ALL'],
                    Output:
                      "{% {'service': $states.input.service, 'status': 'failed', 'error': $states.errorOutput.Error} %}",
                    Next: 'MarkFailed',
                  },
                ],
                Output:
                  "{% {'service': $states.input.service, 'status': ($parse($states.result.Response).success = true) ? 'succeeded' : 'failed'} %}",
                End: true,
              },
              MarkFailed: { Type: 'Pass', End: true },
            },
          },
          Output: "{% {'results': $states.result, 'refresh_origin': $states.input.refresh_origin} %}",
          Next: 'Summarize',
        },
        Summarize: {
          Type: 'Pass',
          Output:
            "{% {'refresh_origin': $states.input.refresh_origin, 'total': $count($states.input.results), 'succeeded': $count($filter($states.input.results, function($r) { $r.status = 'succeeded' })), 'failed': [$filter($states.input.results, function($r) { $r.status != 'succeeded' }).service]} %}",
          End: true,
        },
      },
    };

    const refreshStateMachine = new sfn.StateMachine(this, 'RefreshAllStateMachine', {
      stateMachineName,
      role: refreshStateMachineRole,
      definitionBody: sfn.DefinitionBody.fromString(JSON.stringify(refreshDefinition)),
    });

    // Completion notifications (#126). The old in-agent SNS summary
    // (send_extraction_notification) only fired from the monolithic batch
    // path, which the schedule convergence retires — Map branches use the
    // single-service path, which never notified. Replace it with a native,
    // zero-Lambda EventBridge rule on execution completion; as a bonus this
    // now also notifies for manual batches, which previously never did.
    new events.Rule(this, 'RefreshAllCompletionRule', {
      description: 'Notify on Refresh All orchestration completion (#126)',
      eventPattern: {
        source: ['aws.states'],
        detailType: ['Step Functions Execution Status Change'],
        detail: {
          status: ['SUCCEEDED', 'FAILED', 'TIMED_OUT'],
          stateMachineArn: [refreshStateMachine.stateMachineArn],
        },
      },
      targets: [
        new targets.SnsTopic(this.notificationTopic, {
          message: events.RuleTargetInput.fromText(
            `AWS Services Lifecycle Tracker - Refresh All execution ${events.EventField.fromPath('$.detail.status')}\n` +
            `Execution: ${events.EventField.fromPath('$.detail.executionArn')}\n` +
            `Summary: ${events.EventField.fromPath('$.detail.output')}`
          ),
        }),
      ],
    });

    // Create IAM role for EventBridge Scheduler to invoke AgentCore
    const schedulerRole = new iam.Role(this, 'SchedulerAgentCoreRole', {
      assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com'),
      inlinePolicies: {
        InvokeAgentCore: new iam.PolicyDocument({
          statements: [
            // Health schedule still invokes the runtime directly (single
            // short call, no fan-out need — spec R5/D6).
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
            // Weekly extraction schedule starts the Refresh All state machine
            // instead of invoking the runtime monolithically (#126).
            new iam.PolicyStatement({
              effect: iam.Effect.ALLOW,
              actions: ['states:StartExecution'],
              resources: [refreshStateMachine.stateMachineArn]
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
        // #126: start the Refresh All state machine instead of invoking the
        // runtime monolithically — the weekly run and the UI's manual
        // "Refresh All" now share one orchestration path (spec R4/D6),
        // gaining per-service retries and failure isolation.
        arn: `arn:aws:scheduler:::aws-sdk:sfn:startExecution`,
        roleArn: schedulerRole.roleArn,
        input: JSON.stringify({
          StateMachineArn: refreshStateMachine.stateMachineArn,
          Input: JSON.stringify({
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

    // Schedule for Health collection (every 5 minutes)
    const healthSchedule = new scheduler.CfnSchedule(this, 'HealthCollectionSchedule', {
      name: 'aws-health-events-collection',
      description: 'Poll AWS Health API every 5 minutes',
      scheduleExpression: 'rate(5 minutes)',
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
            action: 'collect_health_events',
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

    new cdk.CfnOutput(this, 'HealthScheduleName', {
      value: healthSchedule.name!,
      description: 'EventBridge Scheduler name for Health events collection (every 5 minutes)'
    });

    new cdk.CfnOutput(this, 'StateMachineArn', {
      value: refreshStateMachine.stateMachineArn,
      description: 'Step Functions state machine orchestrating Refresh All extractions (#126)',
      exportName: 'AWSServicesLifecycleTrackerRefreshStateMachineArn'
    });
  }
}