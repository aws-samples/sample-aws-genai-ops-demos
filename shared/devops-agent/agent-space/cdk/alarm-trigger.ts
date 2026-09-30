/**
 * AlarmTrigger: the Incident RCA trigger chain, alarm -> SNS -> signed webhook call.
 *
 * One Lambda (lambda/alarm-trigger) subscribed to the topics the demo's alarms notify,
 * plus a topic of its own that anything may publish to. On every ALARM notification it
 * builds a generic incident (the alarm as-is, the demo's context lines, the region), signs
 * it with the webhook HMAC secret (x-amzn-event-timestamp / x-amzn-event-signature) and
 * POSTs it to the Agent Space webhook, which starts an investigation. OK notifications are
 * ignored.
 *
 * The secret lives where the Agent Space stack deployed it, possibly another region: pass
 * the imported ISecret and, when it differs, its region. Its VALUE never enters this
 * construct, only the ARN; the Lambda reads it at run time.
 *
 * Mechanism only, used as is. Deploys in the alarms' region (SNS subscriptions are regional).
 */
import * as cdk from 'aws-cdk-lib';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as snsSubscriptions from 'aws-cdk-lib/aws-sns-subscriptions';
import { Construct } from 'constructs';
import * as path from 'path';

export interface AlarmTriggerProps {
  /** The Agent Space webhook URL (from DevOpsAgentSpace.webhookUrl, via the deploy script). */
  readonly webhookUrl: string;
  /** The secret holding the webhook HMAC key (DevOpsAgentSpace.webhookSecret, imported by ARN). */
  readonly webhookSecret: secretsmanager.ISecret;
  /** Region of the secret when it is not this stack's (the Agent Space region). */
  readonly webhookSecretRegion?: string;
  /** Topics whose ALARM notifications become incidents (the demo's alarm topics). */
  readonly topics?: sns.ITopic[];
  /** Label -> value lines added to every incident, e.g. { 'EKS cluster': clusterName }. */
  readonly context?: Record<string, string>;
}

export class AlarmTrigger extends Construct {
  /** A topic of the trigger's own: publish an alarm-shaped message to start an investigation. */
  public readonly topic: sns.Topic;
  public readonly function: lambda.Function;

  constructor(scope: Construct, id: string, props: AlarmTriggerProps) {
    super(scope, id);
    const stack = cdk.Stack.of(this);

    this.function = new lambda.Function(this, 'Function', {
      runtime: lambda.Runtime.PYTHON_3_14,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'alarm-trigger')),
      timeout: cdk.Duration.seconds(30),
      description: 'Forwards CloudWatch alarm notifications to the DevOps Agent webhook as signed incidents',
      environment: {
        WEBHOOK_URL: props.webhookUrl || 'NOT_CONFIGURED',
        SECRET_ARN: props.webhookSecret.secretArn,
        SECRET_REGION: props.webhookSecretRegion ?? stack.region,
        INCIDENT_CONTEXT: JSON.stringify(props.context ?? {}),
      },
    });
    // Explicit statement rather than grantRead: the secret may be an import from another region.
    this.function.addToRolePolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['secretsmanager:GetSecretValue'],
      resources: [props.webhookSecret.secretArn],
    }));

    this.topic = new sns.Topic(this, 'Topic', { displayName: 'DevOps Agent trigger' });
    this.topic.addSubscription(new snsSubscriptions.LambdaSubscription(this.function));
    for (const topic of props.topics ?? []) {
      topic.addSubscription(new snsSubscriptions.LambdaSubscription(this.function));
    }
  }
}
