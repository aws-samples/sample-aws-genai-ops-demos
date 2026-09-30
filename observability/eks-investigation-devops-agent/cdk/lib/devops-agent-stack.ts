import * as cdk from 'aws-cdk-lib';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';
import { AlarmTrigger } from '../../../../shared/devops-agent/agent-space/cdk/alarm-trigger';

/**
 * DevOpsAgentStack — the Incident RCA trigger chain, in the infra region (same as
 * the CloudWatch alarms): the shared AlarmTrigger (SNS → Lambda → signed webhook
 * call) subscribed to the monitoring stack's critical-alarms topic. The Agent
 * Space itself lives in DevOpsAgentSpaceStack, deployed in the DevOps Agent region.
 */
export interface DevOpsAgentStackProps extends cdk.StackProps {
  environment: string;
  projectName: string;
  eksClusterName: string;
  /** Webhook URL returned by the Agent Space stack. */
  webhookUrl: string;
  /**
   * ARN of the Secrets Manager secret holding the webhook HMAC key. The value
   * never passes through CDK context or CloudFormation outputs.
   */
  webhookSecretArn: string;
  /** Region containing webhookSecretArn (normally the Agent Space region). */
  webhookSecretRegion: string;
  criticalAlarmsTopicArn: string;
}

export class DevOpsAgentStack extends cdk.Stack {
  public readonly lambdaFunctionArn: string;
  /** The trigger Lambda's name (the Lab links to its console page). */
  public readonly triggerFunctionName: string;

  constructor(scope: Construct, id: string, props: DevOpsAgentStackProps) {
    super(scope, id, props);

    const { environment, projectName, eksClusterName, webhookUrl, webhookSecretArn, webhookSecretRegion, criticalAlarmsTopicArn } = props;

    // The secret is created by DevOpsAgentSpaceStack, possibly in another region (IAM
    // grants are global; the Lambda reads it from webhookSecretRegion). Phase 1 of the
    // deploy, before the Agent Space outputs exist, synthesizes with a placeholder.
    const webhookSecret = webhookSecretArn
      ? secretsmanager.Secret.fromSecretCompleteArn(this, 'ImportedDevOpsAgentSecret', webhookSecretArn)
      : secretsmanager.Secret.fromSecretNameV2(this, 'UnconfiguredDevOpsAgentSecret', `${projectName}-${environment}/NOT_CONFIGURED`);

    const trigger = new AlarmTrigger(this, 'Trigger', {
      webhookUrl,
      webhookSecret,
      webhookSecretRegion,
      topics: [sns.Topic.fromTopicArn(this, 'ImportedCriticalAlarmsTopic', criticalAlarmsTopicArn)],
      context: { 'EKS cluster': eksClusterName, 'Namespace': 'payment-demo' },
    });

    this.lambdaFunctionArn = trigger.function.functionArn;
    this.triggerFunctionName = trigger.function.functionName;

    new cdk.CfnOutput(this, 'SNSTopicArn', {
      description: 'SNS Topic ARN for DevOps Agent triggers',
      value: trigger.topic.topicArn,
    });

    new cdk.CfnOutput(this, 'LambdaFunctionArn', {
      description: 'Lambda function ARN',
      value: trigger.function.functionArn,
    });

    new cdk.CfnOutput(this, 'AgentSpaceRegion', {
      description: 'Region containing the CDK-managed DevOps Agent Agent Space and webhook secret',
      value: webhookSecretRegion,
    });
  }
}
