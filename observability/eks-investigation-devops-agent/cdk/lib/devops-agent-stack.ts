import * as cdk from 'aws-cdk-lib';
import * as sns from 'aws-cdk-lib/aws-sns';
import * as snsSubscriptions from 'aws-cdk-lib/aws-sns-subscriptions';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as secretsmanager from 'aws-cdk-lib/aws-secretsmanager';
import { Construct } from 'constructs';
import * as path from 'path';

export interface DevOpsAgentStackProps extends cdk.StackProps {
  environment: string;
  projectName: string;
  eksClusterName: string;
  webhookUrl: string;
  webhookSecret: string;
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
      webhookSecret,
      criticalAlarmsTopicArn,
    } = props;

    // -----------------------------------------------------------------------
    // Secrets Manager secret for webhook HMAC key
    // -----------------------------------------------------------------------
    const devOpsAgentSecret = new secretsmanager.Secret(this, 'DevOpsAgentSecret', {
      secretName: `${projectName}-${environment}/devops-agent-webhook-secret`,
      description: 'DevOps Agent webhook HMAC secret key',
      secretStringValue: cdk.SecretValue.unsafePlainText(webhookSecret),
    });

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
    // Lambda function — Python 3.12, handler from extracted code
    // -----------------------------------------------------------------------
    const triggerLambda = new lambda.Function(this, 'DevOpsAgentTriggerLambda', {
      functionName: `${projectName}-${environment}-devops-trigger`,
      runtime: lambda.Runtime.PYTHON_3_12,
      handler: 'index.handler',
      code: lambda.Code.fromAsset(path.join(__dirname, '..', 'lambda', 'devops-agent-trigger')),
      role: lambdaRole,
      timeout: cdk.Duration.seconds(30),
      environment: {
        EKS_CLUSTER_NAME: eksClusterName,
        WEBHOOK_URL: webhookUrl,
        SECRET_ARN: devOpsAgentSecret.secretArn,
        AWS_REGION_NAME: cdk.Aws.REGION,
      },
    });

    // -----------------------------------------------------------------------
    // SNS subscriptions — Lambda subscribes to both topics
    // -----------------------------------------------------------------------
    devOpsAgentTriggerTopic.addSubscription(
      new snsSubscriptions.LambdaSubscription(triggerLambda),
    );

    // Import the critical alarms topic from MonitoringStack and subscribe
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
  }
}
