import * as cdk from 'aws-cdk-lib';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as snsSubscriptions from 'aws-cdk-lib/aws-sns-subscriptions';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';
import * as path from 'path';

/**
 * DevOpsAgentStack — deploys in the infra region (same as CloudWatch alarms).
 *
 * Contains the trigger Lambda (SNS → webhook), Secrets Manager secret, and
 * SNS topic. The Agent Space itself lives in a separate AgentSpaceStack
 * deployed in the DevOps Agent region (e.g. us-east-1).
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

  constructor(scope: Construct, id: string, props: DevOpsAgentStackProps) {
    super(scope, id, props);

    const {
      environment,
      projectName,
      eksClusterName,
      webhookUrl,
      webhookSecretArn,
      webhookSecretRegion,
      criticalAlarmsTopicArn,
    } = props;

    // -----------------------------------------------------------------------
    // Import the webhook secret created by DevOpsAgentSpaceStack.
    //
    // The secret may live in a different region than this stack. IAM grants are
    // global, and the trigger Lambda explicitly creates its Secrets Manager SDK
    // client in webhookSecretRegion. The HMAC value never enters this stack's
    // parameters, context, environment variables, or CloudFormation template.
    // -----------------------------------------------------------------------
    const devOpsAgentSecret = webhookSecretArn
      ? secretsmanager.Secret.fromSecretCompleteArn(
          this,
          'ImportedDevOpsAgentSecret',
          webhookSecretArn,
        )
      : secretsmanager.Secret.fromSecretNameV2(
          this,
          'UnconfiguredDevOpsAgentSecret',
          `${projectName}-${environment}/NOT_CONFIGURED`,
        );

    // -----------------------------------------------------------------------
    // SNS Topic for DevOps Agent triggers
    // -----------------------------------------------------------------------
    const devOpsAgentTriggerTopic = new sns.Topic(this, 'DevOpsAgentTriggerTopic', {
      topicName: `${projectName}-${environment}-devops-agent-trigger`,
      displayName: 'DevOps Agent Trigger',
    });

    // -----------------------------------------------------------------------
    // Lambda execution role — basic execution + Secrets Manager read
    // -----------------------------------------------------------------------
    const lambdaRole = new iam.Role(this, 'DevOpsAgentTriggerLambdaRole', {
      roleName: `${projectName}-${environment}-devops-trigger-role`,
      assumedBy: new iam.ServicePrincipal('lambda.amazonaws.com'),
      managedPolicies: [
        iam.ManagedPolicy.fromAwsManagedPolicyName('service-role/AWSLambdaBasicExecutionRole'),
      ],
    });

    lambdaRole.addToPolicy(new iam.PolicyStatement({
      effect: iam.Effect.ALLOW,
      actions: ['secretsmanager:GetSecretValue'],
      resources: [devOpsAgentSecret.secretArn],
    }));

    // -----------------------------------------------------------------------
    // Lambda function — forwards SNS alarm notifications to DevOps Agent webhook
    // -----------------------------------------------------------------------
    const triggerLambda = new lambda.Function(this, 'DevOpsAgentTriggerLambda', {
      functionName: `${projectName}-${environment}-devops-trigger`,
      runtime: lambda.Runtime.PYTHON_3_14,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'devops-agent-trigger')),
      role: lambdaRole,
      timeout: cdk.Duration.seconds(30),
      environment: {
        EKS_CLUSTER_NAME: eksClusterName,
        WEBHOOK_URL: webhookUrl || 'NOT_CONFIGURED',
        SECRET_ARN: devOpsAgentSecret.secretArn,
        SECRET_REGION: webhookSecretRegion,
        AWS_REGION_NAME: cdk.Aws.REGION,
      },
    });

    // -----------------------------------------------------------------------
    // SNS subscriptions — Lambda subscribes to both topics
    // -----------------------------------------------------------------------
    devOpsAgentTriggerTopic.addSubscription(
      new snsSubscriptions.LambdaSubscription(triggerLambda),
    );

    const criticalAlarmsTopic = sns.Topic.fromTopicArn(
      this, 'ImportedCriticalAlarmsTopic', criticalAlarmsTopicArn,
    );
    criticalAlarmsTopic.addSubscription(
      new snsSubscriptions.LambdaSubscription(triggerLambda),
    );

    // -----------------------------------------------------------------------
    // Expose properties
    // -----------------------------------------------------------------------
    this.lambdaFunctionArn = triggerLambda.functionArn;

    // -----------------------------------------------------------------------
    // CloudFormation Outputs
    // -----------------------------------------------------------------------
    new cdk.CfnOutput(this, 'SNSTopicArn', {
      description: 'SNS Topic ARN for DevOps Agent triggers',
      value: devOpsAgentTriggerTopic.topicArn,
    });

    new cdk.CfnOutput(this, 'LambdaFunctionArn', {
      description: 'Lambda function ARN',
      value: triggerLambda.functionArn,
    });

    new cdk.CfnOutput(this, 'AgentSpaceRegion', {
      description: 'Region containing the CDK-managed DevOps Agent Agent Space and webhook secret',
      value: webhookSecretRegion,
    });
  }
}
