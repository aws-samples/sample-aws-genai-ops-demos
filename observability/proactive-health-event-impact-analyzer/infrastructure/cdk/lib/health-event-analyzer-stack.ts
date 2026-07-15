import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cw_actions from 'aws-cdk-lib/aws-cloudwatch-actions';
import { Construct } from 'constructs';
import { EventIngestion } from './constructs/event-ingestion';
import { InvestigationWorkflow } from './constructs/investigation-workflow';
import { Notification } from './constructs/notification';

/** Valid environment values for the stack */
export const VALID_ENVIRONMENTS = ['production', 'staging'] as const;
export type Environment = typeof VALID_ENVIRONMENTS[number];

export class HealthEventAnalyzerStack extends cdk.Stack {
  /** The resolved deployment environment (production or staging) for this stack */
  public readonly deployEnvironment: Environment;

  constructor(scope: Construct, id: string, props?: cdk.StackProps) {
    super(scope, id, props);

    // Environment configuration
    const envValue = this.node.tryGetContext('environment') ?? 'production';
    if (!VALID_ENVIRONMENTS.includes(envValue)) {
      throw new Error(
        `Invalid environment "${envValue}". Allowed values: ${VALID_ENVIRONMENTS.join(', ')}`
      );
    }
    this.deployEnvironment = envValue as Environment;

    // Stack-level tags (inherited by all child resources)
    cdk.Tags.of(this).add('Project', 'proactive-health-event-impact-analyzer');
    cdk.Tags.of(this).add('Environment', this.deployEnvironment);
    cdk.Tags.of(this).add('ManagedBy', 'cdk');

    // Parameters
    const notificationEmail = new cdk.CfnParameter(this, 'NotificationEmail', {
      type: 'String',
      description: 'Default email address for impact notifications (catch-all)',
      default: '',
    });

    const devOpsAgentWebhookUrl = new cdk.CfnParameter(this, 'DevOpsAgentWebhookUrl', {
      type: 'String',
      description: 'AWS DevOps Agent webhook URL for triggering investigations',
    });

    // SSM Parameter Store paths for secrets (actual SecureString values are
    // created externally by the setup wizard — CDK just references the paths
    // in Lambda environment variables and grants read permissions)
    const ssmParamPrefix = `/health-analyzer/${this.deployEnvironment}`;
    const webhookSecretParamName = `${ssmParamPrefix}/webhook-secret`;
    const slackWebhookParamName = `${ssmParamPrefix}/slack-webhook-url`;
    const msTeamsWebhookParamName = `${ssmParamPrefix}/msteams-webhook-url`;

    // Notification construct (includes teams routing table)
    const notification = new Notification(this, 'Notification', {
      email: notificationEmail.valueAsString,
      deployEnvironment: this.deployEnvironment,
    });

    // Investigation workflow (Step Functions + DevOps Agent)
    const investigation = new InvestigationWorkflow(this, 'Investigation', {
      notificationTopic: notification.topic,
      teamsTable: notification.teamsTable,
      agentSpacesTable: notification.agentSpacesTable,
      devOpsAgentWebhookUrl: devOpsAgentWebhookUrl.valueAsString,
      webhookSecretParamName,
      slackWebhookParamName,
      msTeamsWebhookParamName,
      deployEnvironment: this.deployEnvironment,
      alarmTopic: notification.topic,
    });

    // Event ingestion (EventBridge + Router Lambda)
    const eventIngestion = new EventIngestion(this, 'EventIngestion', {
      stateMachine: investigation.stateMachine,
      deployEnvironment: this.deployEnvironment,
      alarmTopic: notification.topic,
    });

    // ─── Composite CloudWatch Alarm (Requirements 6.1, 6.2, 6.3, 6.6) ────────
    // Single alarm using metric math: SUM of Errors across all Lambdas +
    // ExecutionsFailed on the State Machine. No individual per-Lambda alarms.
    const allLambdas = [
      { id: 'eventRouter', fn: eventIngestion.eventRouter },
      { id: 'investigationTrigger', fn: investigation.investigationTriggerFunction },
      { id: 'opsCenterCreator', fn: investigation.opsCenterCreatorFunction },
      { id: 'notifier', fn: investigation.notifierFunction },
      { id: 'investigationCallback', fn: investigation.investigationCallbackFunction },
    ];

    // Build metric map: one Errors metric per Lambda + ExecutionsFailed for State Machine
    const usingMetrics: Record<string, cloudwatch.IMetric> = {};
    for (const { id, fn } of allLambdas) {
      usingMetrics[id] = fn.metricErrors({ period: cdk.Duration.minutes(5), statistic: 'Sum' });
    }
    usingMetrics['sfnFailed'] = investigation.stateMachine.metricFailed({
      period: cdk.Duration.minutes(5),
      statistic: 'Sum',
    });

    const compositeMetric = new cloudwatch.MathExpression({
      expression: 'eventRouter + investigationTrigger + opsCenterCreator + notifier + investigationCallback + sfnFailed',
      usingMetrics,
      period: cdk.Duration.minutes(5),
    });

    const compositeAlarm = new cloudwatch.Alarm(this, 'CompositeHealthAlarm', {
      alarmName: 'health-event-analyzer-composite-alarm',
      alarmDescription: 'Composite alarm: fires when any Lambda errors or Step Functions execution failures occur',
      metric: compositeMetric,
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    compositeAlarm.addAlarmAction(new cw_actions.SnsAction(notification.topic));
    compositeAlarm.addOkAction(new cw_actions.SnsAction(notification.topic));

    // ─── SNS Topic Resource Policy (Requirements 14.1–14.4, 4.1) ──────────────
    // Applied at stack level because it requires the Notifier role ARN from
    // the InvestigationWorkflow construct.
    notification.topic.addToResourcePolicy(new iam.PolicyStatement({
      sid: 'AllowNotifierAndAlarms',
      effect: iam.Effect.ALLOW,
      principals: [
        new iam.ArnPrincipal(investigation.notifierRoleArn),
        new iam.ServicePrincipal('cloudwatch.amazonaws.com'),
      ],
      actions: ['sns:Publish'],
      resources: [notification.topic.topicArn],
      conditions: {
        StringEquals: {
          'aws:SourceAccount': this.account,
        },
      },
    }));

    notification.topic.addToResourcePolicy(new iam.PolicyStatement({
      sid: 'DenyExternalPublish',
      effect: iam.Effect.DENY,
      principals: [new iam.AnyPrincipal()],
      actions: ['sns:Publish'],
      resources: [notification.topic.topicArn],
      conditions: {
        StringNotEquals: {
          'aws:PrincipalAccount': this.account,
        },
        Bool: {
          'aws:PrincipalIsAWSService': 'false',
        },
      },
    }));

    notification.topic.addToResourcePolicy(new iam.PolicyStatement({
      sid: 'DenyInsecureTransport',
      effect: iam.Effect.DENY,
      principals: [new iam.AnyPrincipal()],
      actions: [
        'sns:Publish',
        'sns:Subscribe',
      ],
      resources: [notification.topic.topicArn],
      conditions: {
        Bool: {
          'aws:SecureTransport': 'false',
        },
      },
    }));

    notification.topic.addToResourcePolicy(new iam.PolicyStatement({
      sid: 'RestrictSubscriptions',
      effect: iam.Effect.ALLOW,
      principals: [new iam.AccountPrincipal(this.account)],
      actions: ['sns:Subscribe'],
      resources: [notification.topic.topicArn],
    }));

    // Outputs
    new cdk.CfnOutput(this, 'NotificationTopicArn', {
      value: notification.topic.topicArn,
      description: 'SNS topic ARN for health event notifications (catch-all)',
    });

    new cdk.CfnOutput(this, 'TeamsTableName', {
      value: notification.teamsTable.tableName,
      description: 'DynamoDB table for team notification configuration',
    });

    new cdk.CfnOutput(this, 'AgentSpacesTableName', {
      value: notification.agentSpacesTable.tableName,
      description: 'DynamoDB table for multi-account DevOps Agent space routing',
    });

    new cdk.CfnOutput(this, 'StateMachineArn', {
      value: investigation.stateMachine.stateMachineArn,
      description: 'Step Functions state machine ARN',
    });
  }
}
